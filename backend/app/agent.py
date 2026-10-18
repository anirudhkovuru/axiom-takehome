import json
import re
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

from .artifacts import ArtifactManager
from .config import Settings
from .data_access import DataAccess
from .python_runtime import PythonRuntime
from .store import Store


SYSTEM_PROMPT = """
You are Axiom Workbench, a careful scientific data analyst working over a DuckDB database of
experimental measurements for 33 anonymized compounds. Your job is to turn a scientist's question
into a reproducible analysis and a concise evidence-backed answer.

Workflow requirements:
1. Call set_analysis_plan exactly once before querying data.
2. Inspect the embedded dataset documentation or feature inventory before choosing columns.
3. Use read-only SQL for retrieval and Python only when statistics, clustering, or figures add value.
4. Save the most useful result table or figure as an artifact.
5. In the final answer, separate observations from interpretation and state coverage or limitations.
6. Refer to compounds only by compound_id. Never infer hidden chemical identities.
7. Work efficiently: usually use 3-6 tool calls, avoid repeating equivalent queries, and synthesize a
   final answer as soon as enough evidence exists. Do not keep exploring merely to exhaust possibilities.

Dataset rules:
- data_2d_phh_raw and data_truvivo_raw are long format. Feature names are in readout.
- Prefer value_unclipped for readouts that can fall below the DMSO baseline; bounded viability may use value.
- Exclude qc_keep = false for standard ADME analysis and handle NULL explicitly.
- Morphology embeddings are 256-dimensional. Filter assay, channel, and granularity before comparing them.
- Interpret RNA differential expression with log2_fold_change and padj.
- Interpret GSEA with nes and fdr_qvalue. Enrichment supports a mechanistic hypothesis, not causal proof.
- Morphology covers 24/33 compounds overall; TruVivo morphology covers 12. Transcriptomics covers 31/33.
- There are only 33 compounds. Avoid predictive or population-level claims.

Python runtime:
- A read-only DuckDB connection named con is already available.
- pandas as pd, numpy as np, matplotlib.pyplot as plt, DB_PATH, and ARTIFACT_DIR are available.
- Use save_table(frame, filename) and save_figure(figure, filename) to create artifacts.
- Do not open files, access the network, or create subprocesses.

Writing style:
Lead with the result. Use plain scientific language, short sections, and concrete numbers. Mention the
analysis choice, strongest evidence, and limitations. Do not overstate mechanism or causality.
""".strip()


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "set_analysis_plan",
        "description": "Publish a short analysis plan before using data tools.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "steps": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 6,
                    "items": {
                        "type": "object",
                        "properties": {"label": {"type": "string"}},
                        "required": ["label"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "steps"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "inspect_dataset",
        "description": "Search embedded table documentation and feature inventory before writing a query.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "A modality, table, feature, or 'overview'.",
                }
            },
            "required": ["topic"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_sql",
        "description": "Run one read-only DuckDB query. Optionally save the returned rows as a CSV artifact.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "purpose": {"type": "string"},
                "save_as": {
                    "type": ["string", "null"],
                    "description": "CSV filename when the result should be downloadable, otherwise null.",
                },
            },
            "required": ["query", "purpose", "save_as"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_python_analysis",
        "description": "Run controlled scientific Python for statistics, clustering, or figures.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "purpose": {"type": "string"},
            },
            "required": ["code", "purpose"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


class AgentRunner:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        data: DataAccess,
        artifacts: ArtifactManager,
        python_runtime: PythonRuntime,
    ):
        self.settings = settings
        self.store = store
        self.data = data
        self.artifacts = artifacts
        self.python_runtime = python_runtime

    def run(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        self.store.set_run_status(run_id, "running")
        self.store.emit(
            run_id,
            "run.started",
            {"run_id": run_id, "model": run["model"], "demo_mode": self.settings.use_demo_agent},
        )
        try:
            if self.settings.use_demo_agent:
                self._run_demo(run)
            else:
                self._run_openai(run)
        except Exception as exc:
            error = str(exc)
            self.store.set_run_status(run_id, "failed", error=error)
            self.store.emit(run_id, "run.failed", {"run_id": run_id, "error": error})

    def _run_openai(self, run: dict[str, Any]) -> None:
        client = OpenAI(api_key=self.settings.openai_api_key)
        input_items: list[Any] = self.store.message_history(run["chat_id"])
        assistant_text = ""

        while True:
            response = None
            stream = client.responses.create(
                model=self.settings.openai_model,
                instructions=SYSTEM_PROMPT,
                input=input_items,
                tools=TOOLS,
                parallel_tool_calls=False,
                reasoning={"effort": "low"},
                store=False,
                stream=True,
            )
            for event in stream:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_text.delta":
                    delta = event.delta
                    assistant_text += delta
                    self.store.emit(run["id"], "assistant.delta", {"delta": delta})
                elif event_type == "response.completed":
                    response = event.response
                elif event_type == "error":
                    raise RuntimeError(getattr(event, "message", "OpenAI streaming error"))

            if response is None:
                raise RuntimeError("The model stream ended without a completed response")

            input_items.extend(response.output)
            function_calls = [item for item in response.output if item.type == "function_call"]
            if not function_calls:
                if not assistant_text.strip():
                    assistant_text = response.output_text
                break

            for function_call in function_calls:
                try:
                    arguments = json.loads(function_call.arguments)
                    output = self._execute_tool(
                        run_id=run["id"],
                        chat_id=run["chat_id"],
                        tool_name=function_call.name,
                        arguments=arguments,
                    )
                except Exception as exc:
                    output = {"error": str(exc), "tool": function_call.name}
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": function_call.call_id,
                        "output": json.dumps(output, default=str),
                    }
                )
        self._complete_run(run, assistant_text.strip())

    def _execute_tool(
        self,
        run_id: str,
        chat_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name == "set_analysis_plan":
            payload = {"title": arguments["title"], "steps": arguments["steps"]}
            self.store.emit(run_id, "plan.created", payload)
            return {"status": "plan published"}

        title = arguments.get("purpose") or f"Inspect {arguments.get('topic', 'dataset')}"
        step = self.store.create_step(run_id, tool_name, title, arguments)
        self.store.emit(run_id, "step.started", step)
        try:
            if tool_name == "inspect_dataset":
                result = self.data.inspect(arguments["topic"])
                summary = f"Found {len(result['tables'])} matching tables and {len(result['features'])} features"
            elif tool_name == "run_sql":
                result = self.data.query(arguments["query"])
                sql_artifact = self.artifacts.write_sql(
                    run_id, chat_id, step["id"], arguments["query"]
                )
                self.store.emit(run_id, "artifact.created", sql_artifact)
                if arguments.get("save_as"):
                    csv_artifact = self.artifacts.write_csv(
                        run_id,
                        chat_id,
                        step["id"],
                        arguments["save_as"],
                        result["columns"],
                        result["rows"],
                    )
                    self.store.emit(run_id, "artifact.created", csv_artifact)
                summary = f"Returned {result['row_count']} rows"
            elif tool_name == "run_python_analysis":
                runtime_result = self.python_runtime.execute(
                    arguments["code"], self.artifacts.run_directory(run_id)
                )
                code_artifact = self.artifacts.register(
                    runtime_result["script_path"], run_id, chat_id, step["id"]
                )
                self.store.emit(run_id, "artifact.created", code_artifact)
                generated_artifacts = []
                for path in runtime_result["generated_files"]:
                    artifact = self.artifacts.register(path, run_id, chat_id, step["id"])
                    generated_artifacts.append(artifact)
                    self.store.emit(run_id, "artifact.created", artifact)
                result = {
                    "return_code": runtime_result["return_code"],
                    "output": runtime_result["output"],
                    "artifacts": generated_artifacts,
                }
                if runtime_result["return_code"] != 0:
                    raise RuntimeError(runtime_result["output"] or "Python analysis failed")
                summary = f"Created {len(generated_artifacts)} analysis artifacts"
            else:
                raise ValueError(f"Unknown tool: {tool_name}")
        except Exception as exc:
            self.store.complete_step(step["id"], "failed", str(exc))
            self.store.emit(
                run_id,
                "step.failed",
                {**step, "status": "failed", "output_summary": str(exc)},
            )
            raise

        self.store.complete_step(step["id"], "completed", summary)
        self.store.emit(
            run_id,
            "step.completed",
            {**step, "status": "completed", "output_summary": summary},
        )
        return result

    def _run_demo(self, run: dict[str, Any]) -> None:
        prompt = self.store.message_history(run["chat_id"])[-1]["content"]
        lowered = prompt.lower()
        if any(word in lowered for word in ("scatter", "bioactivity", "viability")):
            answer = self._demo_scatter(run)
        elif any(word in lowered for word in ("cluster", "phenotypic", "morphology")):
            answer = self._demo_clusters(run)
        elif "compare" in lowered or re.search(r"compound\s+\d+.*compound\s+\d+", lowered):
            answer = self._demo_compare(run, prompt)
        elif any(word in lowered for word in ("mechanism", "toxic", "toxicity")):
            answer = self._demo_mechanism(run, prompt)
        else:
            answer = self._demo_overview(run)
        self._emit_text(run["id"], answer)
        self._complete_run(run, answer)

    def _demo_scatter(self, run: dict[str, Any]) -> str:
        self._plan(
            run["id"],
            "Relate bioactivity to viability",
            ["Identify comparable fitted readouts", "Join compound-level AUC values", "Plot and quantify the relationship"],
        )
        self._execute_tool(run["id"], run["chat_id"], "inspect_dataset", {"topic": "2d phh bioactivity viability"})
        code = """
query = '''
SELECT b.compound_id, b.auc AS bioactivity_auc, v.auc AS viability_auc
FROM data_2d_phh_sigmoid_fits b
JOIN data_2d_phh_sigmoid_fits v USING (compound_id)
WHERE b.readout = 'bioactivity_svc_tvn_pca_platenorm_coral_regnorm_all_n_components_256'
  AND v.readout = 'mtt_ridge_norm_inv'
ORDER BY b.compound_id
'''
df = con.sql(query).df().dropna()
correlation = df['bioactivity_auc'].corr(df['viability_auc'])
fig, ax = plt.subplots(figsize=(8, 6))
ax.scatter(df['bioactivity_auc'], df['viability_auc'], s=55, color='#236b52', alpha=0.85)
for row in df.itertuples():
    ax.annotate(str(row.compound_id), (row.bioactivity_auc, row.viability_auc), xytext=(4, 3), textcoords='offset points', fontsize=7)
ax.set_xlabel('2D PHH bioactivity AUC')
ax.set_ylabel('2D PHH viability AUC')
ax.set_title('Bioactivity versus viability across compounds')
ax.grid(alpha=0.2)
save_table(df, 'bioactivity-vs-viability.csv')
save_figure(fig, 'bioactivity-vs-viability.png')
print(f'n={len(df)}; Pearson r={correlation:.3f}')
""".strip()
        result = self._execute_tool(
            run["id"],
            run["chat_id"],
            "run_python_analysis",
            {"purpose": "Calculate and plot compound-level bioactivity versus viability", "code": code},
        )
        output = result.get("output", "")
        return f"""## Bioactivity and viability move together

I compared compound-level dose-response AUCs from the 2D PHH assay, using the all-channel morphology bioactivity score and the MTT viability readout. **{output}**

The figure labels each point by `compound_id`, and the accompanying CSV contains the exact values used. This is an association across fitted assay summaries; it does not establish that morphological activity causes loss of viability. Curve-fit quality and the choice of AUC also affect the relationship."""

    def _demo_clusters(self, run: dict[str, Any]) -> str:
        self._plan(
            run["id"],
            "Map phenotypic similarity",
            ["Select comparable morphology profiles", "Cluster standardized embeddings", "Visualize clusters and report coverage"],
        )
        self._execute_tool(run["id"], run["chat_id"], "inspect_dataset", {"topic": "embedding morphology"})
        code = """
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

df = con.sql('''
SELECT compound_id, embedding
FROM data_embedding_profiles
WHERE assay = '2d_phh' AND channel = 'all' AND granularity = 'compound_dose_range'
ORDER BY compound_id
''').df()
matrix = np.vstack(df['embedding'].to_numpy())
scaled = StandardScaler().fit_transform(matrix)
pca = PCA(n_components=2, random_state=42)
coords = pca.fit_transform(scaled)
labels = KMeans(n_clusters=4, random_state=42, n_init=30).fit_predict(scaled)
result = pd.DataFrame({'compound_id': df['compound_id'], 'pc1': coords[:, 0], 'pc2': coords[:, 1], 'cluster': labels + 1})
score = silhouette_score(scaled, labels)
fig, ax = plt.subplots(figsize=(8, 6))
scatter = ax.scatter(result.pc1, result.pc2, c=result.cluster, cmap='viridis', s=70, alpha=0.9)
for row in result.itertuples():
    ax.annotate(str(row.compound_id), (row.pc1, row.pc2), xytext=(4, 3), textcoords='offset points', fontsize=7)
ax.set_xlabel(f'PC1 ({pca.explained_variance_ratio_[0]:.1%} variance)')
ax.set_ylabel(f'PC2 ({pca.explained_variance_ratio_[1]:.1%} variance)')
ax.set_title('2D PHH morphology clusters')
ax.grid(alpha=0.2)
save_table(result, 'phenotypic-clusters.csv')
save_figure(fig, 'phenotypic-clusters.png')
print(f'n={len(result)} compounds; silhouette={score:.3f}; 2-PC variance={pca.explained_variance_ratio_.sum():.1%}')
""".strip()
        result = self._execute_tool(
            run["id"],
            run["chat_id"],
            "run_python_analysis",
            {"purpose": "Cluster dose-range morphology profiles and create a PCA map", "code": code},
        )
        return f"""## Four candidate phenotype groups emerge

I clustered the 256-dimensional, all-channel 2D PHH dose-range profiles after feature standardization. **{result.get('output', '')}**

The downloadable membership table preserves the cluster assignment and PCA coordinates. Treat these as exploratory groups: morphology is available for 24 of 33 compounds, the two-dimensional view is a projection, and true consistency should be checked across channels, doses, and the 12 compounds with TruVivo morphology."""

    def _demo_compare(self, run: dict[str, Any], prompt: str) -> str:
        compound_ids = [int(value) for value in re.findall(r"\b(?:compound\s*)?(\d{1,2})\b", prompt, flags=re.I)]
        first, second = (compound_ids + [1, 2])[:2]
        self._plan(
            run["id"],
            f"Compare compounds {first} and {second}",
            ["Compare fitted cell-response summaries", "Add QC-passing ADME context", "Rank the largest differences"],
        )
        result = self._execute_tool(
            run["id"],
            run["chat_id"],
            "run_sql",
            {
                "purpose": f"Build a side-by-side assay comparison for compounds {first} and {second}",
                "save_as": f"compound-{first}-vs-{second}.csv",
                "query": f"""
                WITH assay AS (
                    SELECT compound_id, '2D PHH' AS modality, readout, auc AS value, 'auc' AS measure
                    FROM data_2d_phh_sigmoid_fits WHERE compound_id IN ({first}, {second})
                    UNION ALL
                    SELECT compound_id, 'TruVivo' AS modality, readout, auc AS value, 'auc' AS measure
                    FROM data_truvivo_sigmoid_fits WHERE compound_id IN ({first}, {second})
                ), adme AS (
                    SELECT compound_id, 'HLM' AS modality, readout, value, 'observed' AS measure
                    FROM data_hlm_clearance WHERE compound_id IN ({first}, {second}) AND qc_keep = true
                    UNION ALL
                    SELECT compound_id, 'Caco-2' AS modality, readout, value, 'observed' AS measure
                    FROM data_caco2_permeability WHERE compound_id IN ({first}, {second}) AND qc_keep = true
                )
                SELECT * FROM assay UNION ALL SELECT * FROM adme
                ORDER BY modality, readout, compound_id
                """,
            },
        )
        return f"""## Compounds {first} and {second} are ready for side-by-side review

I assembled **{result['row_count']} comparable assay summaries** spanning 2D PHH, TruVivo, and QC-passing ADME measurements. The CSV keeps the original readout names and values so the comparison is fully inspectable.

The next scientific step is to rank readouts by the absolute difference between the two compounds and then check whether those differences appear before viability declines. A larger AUC difference is descriptive, not automatically evidence of greater clinical toxicity, and missing QC-passing ADME rows should be treated as missing rather than favorable."""

    def _demo_mechanism(self, run: dict[str, Any], prompt: str) -> str:
        match = re.search(r"compound\s*(\d{1,2})", prompt, flags=re.I)
        compound_id = int(match.group(1)) if match else 1
        self._plan(
            run["id"],
            f"Investigate compound {compound_id}",
            ["Review cellular dose-response phenotypes", "Find significant enriched pathways", "Triangulate a mechanistic hypothesis"],
        )
        self._execute_tool(run["id"], run["chat_id"], "inspect_dataset", {"topic": "truvivo gsea mechanism"})
        result = self._execute_tool(
            run["id"],
            run["chat_id"],
            "run_sql",
            {
                "purpose": f"Collect strongest pathway and cellular evidence for compound {compound_id}",
                "save_as": f"compound-{compound_id}-mechanism-evidence.csv",
                "query": f"""
                WITH pathways AS (
                    SELECT 'GSEA' AS evidence_type, set_id AS feature, nes AS effect, fdr_qvalue AS significance,
                           compound_concentration AS concentration_um
                    FROM data_transcriptomics_gsea
                    WHERE compound_id = {compound_id} AND fdr_qvalue <= 0.05
                    QUALIFY row_number() OVER (ORDER BY abs(nes) DESC) <= 20
                ), phenotypes AS (
                    SELECT 'TruVivo' AS evidence_type, readout AS feature, auc AS effect, NULL AS significance,
                           NULL AS concentration_um
                    FROM data_truvivo_sigmoid_fits
                    WHERE compound_id = {compound_id}
                )
                SELECT * FROM pathways UNION ALL SELECT * FROM phenotypes
                ORDER BY evidence_type, abs(effect) DESC
                """,
            },
        )
        return f"""## Compound {compound_id} has a mechanism evidence packet

I collected **{result['row_count']} high-priority observations** from significant GSEA pathways and fitted TruVivo phenotypes. The exported table is the appropriate basis for a mechanism hypothesis because it combines pathway direction and significance with cellular response summaries.

The main mechanism should be assigned only when several independent signals agree—for example, a pathway enrichment accompanied by the matching ER-stress, lipid, or bile-canaliculi phenotype. This dataset supports mechanistic interpretation, not causal or clinical confirmation."""

    def _demo_overview(self, run: dict[str, Any]) -> str:
        self._plan(
            run["id"],
            "Profile the available evidence",
            ["Inspect documented modalities", "Summarize compound coverage", "Recommend analysis starting points"],
        )
        result = self._execute_tool(run["id"], run["chat_id"], "inspect_dataset", {"topic": "overview"})
        return f"""## The dataset is ready for multimodal analysis

I found **{len(result['tables'])} documented tables** and **{len(result['features'])} indexed feature groups** covering liver-cell responses, morphology, transcriptomics, and ADME for 33 anonymized compounds.

Good starting analyses are: phenotype clustering from dose-range embeddings, compound comparisons across fitted assay summaries, mechanism hypotheses combining GSEA with TruVivo phenotypes, and bioactivity-versus-viability plots. Ask one of those questions and I’ll produce inspectable code, tables, and figures."""

    def _plan(self, run_id: str, title: str, labels: list[str]) -> None:
        self.store.emit(
            run_id,
            "plan.created",
            {"title": title, "steps": [{"label": label} for label in labels]},
        )

    def _emit_text(self, run_id: str, text: str) -> None:
        words = text.split(" ")
        for index in range(0, len(words), 8):
            chunk = " ".join(words[index : index + 8])
            if index + 8 < len(words):
                chunk += " "
            self.store.emit(run_id, "assistant.delta", {"delta": chunk})
            time.sleep(0.015)

    def _complete_run(self, run: dict[str, Any], assistant_text: str) -> None:
        if not assistant_text:
            assistant_text = "The analysis completed, but no narrative response was produced. Review the generated artifacts."
        message = self.store.add_message(
            run["chat_id"], "assistant", assistant_text, run_id=run["id"]
        )
        chat = self.store.get_chat(run["chat_id"])
        if chat["title"] == "New analysis":
            first_user = next((item["content"] for item in chat["messages"] if item["role"] == "user"), "Analysis")
            title = re.sub(r"\s+", " ", first_user).strip()[:54]
            self.store.update_chat_title(run["chat_id"], title)
        self.store.set_run_status(run["id"], "completed")
        self.store.emit(
            run["id"],
            "run.completed",
            {
                "run_id": run["id"],
                "message": message,
                "artifact_count": len(self.store.list_run_artifacts(run["id"])),
            },
        )
