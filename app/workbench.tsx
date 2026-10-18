"use client";

import {
  Activity,
  Archive,
  ArrowDownToLine,
  Atom,
  BarChart3,
  Check,
  ChevronRight,
  CircleAlert,
  Code2,
  Database,
  FileSpreadsheet,
  FlaskConical,
  FolderKanban,
  LoaderCircle,
  Menu,
  PanelRightClose,
  PanelRightOpen,
  Pencil,
  Plus,
  Send,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import Image from "next/image";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { API_BASE, api } from "./lib/api";
import type {
  AnalysisPlan,
  Artifact,
  ChatDetail,
  ChatSummary,
  DatasetSummary,
  Experiment,
  Health,
  Step,
} from "./types";


const STARTERS = [
  {
    title: "Map phenotypic clusters",
    prompt: "Find consistent phenotypic clusters of compounds using morphology profiles.",
    icon: Atom,
  },
  {
    title: "Investigate a mechanism",
    prompt: "What is the strongest supported mechanism of toxicity for compound 1?",
    icon: FlaskConical,
  },
  {
    title: "Compare two compounds",
    prompt: "Compare compound 4 and compound 18. Why might their toxicity profiles differ?",
    icon: Activity,
  },
  {
    title: "Relate two readouts",
    prompt: "Create a scatterplot of bioactivity versus cell viability.",
    icon: BarChart3,
  },
];


function formatTime(value: string) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}


function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}


function uniqueArtifacts(artifacts: Artifact[]) {
  return Array.from(new Map(artifacts.map((artifact) => [artifact.id, artifact])).values());
}


export function Workbench() {
  const [health, setHealth] = useState<Health | null>(null);
  const [dataset, setDataset] = useState<DatasetSummary | null>(null);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [activeChat, setActiveChat] = useState<ChatDetail | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [plan, setPlan] = useState<AnalysisPlan | null>(null);
  const [liveSteps, setLiveSteps] = useState<Step[]>([]);
  const [liveArtifacts, setLiveArtifacts] = useState<Artifact[]>([]);
  const [liveText, setLiveText] = useState("");
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editingChatId, setEditingChatId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [deletingChat, setDeletingChat] = useState<ChatSummary | null>(null);
  const [chatActionPending, setChatActionPending] = useState(false);
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(true);
  const eventSourceRef = useRef<EventSource | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const refreshCollections = useCallback(async () => {
    const [nextChats, nextExperiments] = await Promise.all([api.chats(), api.experiments()]);
    setChats(nextChats);
    setExperiments(nextExperiments);
  }, []);

  const loadChat = useCallback(async (chatId: string) => {
    const detail = await api.chat(chatId);
    setActiveChat(detail);
    setLiveArtifacts([]);
    setLeftOpen(false);
    const activeRun = detail.runs.find((run) => ["queued", "running"].includes(run.status));
    return activeRun?.id ?? null;
  }, []);

  const finishStream = useCallback(
    async (chatId: string) => {
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      setActiveRunId(null);
      setPlan(null);
      setLiveSteps([]);
      setLiveText("");
      await Promise.all([loadChat(chatId), refreshCollections()]);
    },
    [loadChat, refreshCollections],
  );

  const subscribeToRun = useCallback(
    (runId: string, chatId: string) => {
      eventSourceRef.current?.close();
      setActiveRunId(runId);
      setPlan(null);
      setLiveSteps([]);
      setLiveText("");
      const source = new EventSource(`${API_BASE}/api/runs/${runId}/events`);
      eventSourceRef.current = source;

      const parse = <T,>(event: MessageEvent<string>) => JSON.parse(event.data) as T;
      source.addEventListener("plan.created", (event) => setPlan(parse<AnalysisPlan>(event as MessageEvent<string>)));
      source.addEventListener("step.started", (event) => {
        const step = parse<Step>(event as MessageEvent<string>);
        setLiveSteps((current) => [...current.filter((item) => item.id !== step.id), step]);
      });
      const updateStep = (event: Event) => {
        const step = parse<Step>(event as MessageEvent<string>);
        setLiveSteps((current) => [...current.filter((item) => item.id !== step.id), step].sort((a, b) => a.ordinal - b.ordinal));
      };
      source.addEventListener("step.completed", updateStep);
      source.addEventListener("step.failed", updateStep);
      source.addEventListener("artifact.created", (event) => {
        const artifact = parse<Artifact>(event as MessageEvent<string>);
        setLiveArtifacts((current) => uniqueArtifacts([artifact, ...current]));
      });
      source.addEventListener("assistant.delta", (event) => {
        const payload = parse<{ delta: string }>(event as MessageEvent<string>);
        setLiveText((current) => current + payload.delta);
      });
      source.addEventListener("run.completed", () => void finishStream(chatId));
      source.addEventListener("run.failed", (event) => {
        const payload = parse<{ error: string }>(event as MessageEvent<string>);
        setError(payload.error);
        void finishStream(chatId);
      });
    },
    [finishStream],
  );

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      try {
        const [nextHealth, nextDataset, nextChats, nextExperiments] = await Promise.all([
          api.health(),
          api.dataset(),
          api.chats(),
          api.experiments(),
        ]);
        if (cancelled) return;
        setHealth(nextHealth);
        setDataset(nextDataset);
        setChats(nextChats);
        setExperiments(nextExperiments);
        if (nextChats.length) {
          const runId = await loadChat(nextChats[0].id);
          if (runId && !cancelled) subscribeToRun(runId, nextChats[0].id);
        }
      } catch (nextError) {
        if (!cancelled) setError(nextError instanceof Error ? nextError.message : "Unable to load workspace");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void bootstrap();
    return () => {
      cancelled = true;
      eventSourceRef.current?.close();
    };
  }, [loadChat, subscribeToRun]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [activeChat?.messages.length, liveText, liveSteps.length]);

  const allArtifacts = useMemo(
    () => uniqueArtifacts([...liveArtifacts, ...(activeChat?.artifacts ?? [])]),
    [activeChat?.artifacts, liveArtifacts],
  );

  async function createNewChat() {
    try {
      setError(null);
      const created = await api.createChat();
      await refreshCollections();
      setActiveChat(created);
      setPlan(null);
      setLiveText("");
      setLiveSteps([]);
      setLiveArtifacts([]);
      setLeftOpen(false);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Could not create chat");
    }
  }

  async function chooseChat(chatId: string) {
    if (activeRunId) return;
    try {
      setError(null);
      const runId = await loadChat(chatId);
      if (runId) subscribeToRun(runId, chatId);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Could not load chat");
    }
  }

  function startRenamingChat(chat: ChatSummary) {
    setEditingChatId(chat.id);
    setEditingTitle(chat.title);
  }

  async function renameChat(event: FormEvent, chatId: string) {
    event.preventDefault();
    const title = editingTitle.trim();
    if (!title || chatActionPending) return;
    try {
      setChatActionPending(true);
      setError(null);
      const updated = await api.renameChat(chatId, title);
      if (activeChat?.id === chatId) setActiveChat(updated);
      setEditingChatId(null);
      await refreshCollections();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Could not rename chat");
    } finally {
      setChatActionPending(false);
    }
  }

  async function deleteSelectedChat() {
    if (!deletingChat || chatActionPending) return;
    const chatId = deletingChat.id;
    const deletingActiveChat = activeChat?.id === chatId;
    try {
      setChatActionPending(true);
      setError(null);
      await api.deleteChat(chatId);
      setDeletingChat(null);
      setEditingChatId((current) => current === chatId ? null : current);

      const [nextChats, nextExperiments] = await Promise.all([api.chats(), api.experiments()]);
      setChats(nextChats);
      setExperiments(nextExperiments);

      if (deletingActiveChat) {
        eventSourceRef.current?.close();
        eventSourceRef.current = null;
        setActiveChat(null);
        setActiveRunId(null);
        setPlan(null);
        setLiveText("");
        setLiveSteps([]);
        setLiveArtifacts([]);
        if (nextChats.length) {
          const runId = await loadChat(nextChats[0].id);
          if (runId) subscribeToRun(runId, nextChats[0].id);
        }
      }
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Could not delete chat");
    } finally {
      setChatActionPending(false);
    }
  }

  async function sendMessage(content: string) {
    const trimmed = content.trim();
    if (!trimmed || activeRunId) return;
    try {
      setError(null);
      let chat = activeChat;
      if (!chat) {
        chat = await api.createChat();
        setActiveChat(chat);
      }
      const response = await api.sendMessage(chat.id, trimmed);
      setActiveChat((current) =>
        current ? { ...current, messages: [...current.messages, response.message] } : current,
      );
      setInput("");
      setLiveText("");
      setLiveSteps([]);
      setLiveArtifacts([]);
      subscribeToRun(response.run.id, chat.id);
      await refreshCollections();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Could not start analysis");
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void sendMessage(input);
  }

  async function saveCurrentExperiment() {
    if (!activeChat || activeChat.messages.length === 0) return;
    try {
      setSaving(true);
      setError(null);
      const name = activeChat.title === "New analysis" ? "Saved analysis" : activeChat.title;
      await api.saveExperiment(activeChat.id, name);
      await refreshCollections();
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Could not save experiment");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <main className="loading-screen">
        <div className="brand-mark"><Atom size={22} /></div>
        <p>Preparing the scientific workspace</p>
        <LoaderCircle className="spin" size={18} />
      </main>
    );
  }

  return (
    <main className="workbench-shell">
      <aside className={`left-rail ${leftOpen ? "open" : ""}`}>
        <div className="brand-row">
          <div className="brand-mark"><Atom size={19} /></div>
          <div>
            <strong>Axiom</strong>
            <span>Workbench</span>
          </div>
          <button className="icon-button rail-close" onClick={() => setLeftOpen(false)} aria-label="Close navigation"><X size={18} /></button>
        </div>

        <button className="new-analysis" onClick={() => void createNewChat()}>
          <Plus size={17} /> New analysis
        </button>

        <nav className="rail-content" aria-label="Analysis history">
          <section>
            <div className="rail-section-title"><span>Chats</span><span>{chats.length}</span></div>
            <div className="rail-list">
              {chats.map((chat) => {
                const isActive = activeChat?.id === chat.id;
                const cannotDelete = isActive && Boolean(activeRunId);
                return (
                  <div key={chat.id} className={`rail-item ${isActive ? "active" : ""}`}>
                    <button className="rail-item-main" onClick={() => void chooseChat(chat.id)}>
                      <span className="rail-item-title">{chat.title}</span>
                      <span className="rail-item-meta">{chat.message_count} messages · {formatTime(chat.updated_at)}</span>
                    </button>
                    <button
                      className="chat-delete-button"
                      onClick={() => setDeletingChat(chat)}
                      disabled={cannotDelete}
                      aria-label={`Delete ${chat.title}`}
                      title={cannotDelete ? "Wait for this analysis to finish" : "Delete chat"}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                );
              })}
              {!chats.length && <p className="rail-empty">Your analysis history will appear here.</p>}
            </div>
          </section>

          <section>
            <div className="rail-section-title"><span>Experiments</span><span>{experiments.length}</span></div>
            <div className="rail-list">
              {experiments.map((experiment) => (
                <div className="experiment-item" key={experiment.id}>
                  <button onClick={() => void chooseChat(experiment.chat_id)}>
                    <FolderKanban size={15} />
                    <span>
                      <strong>{experiment.name}</strong>
                      <small>{experiment.artifact_count} artifacts</small>
                    </span>
                  </button>
                  <a
                    href={`${API_BASE}/api/experiments/${experiment.id}/export`}
                    aria-label={`Export ${experiment.name}`}
                    title="Export reproducibility bundle"
                  ><ArrowDownToLine size={14} /></a>
                </div>
              ))}
            </div>
          </section>
        </nav>

        <div className="dataset-mini">
          <div className="dataset-mini-head"><Database size={15} /><span>Connected dataset</span></div>
          <strong>{dataset?.compound_count ?? 0} compounds</strong>
          <span>{dataset?.tables.length ?? 0} documented tables</span>
        </div>
      </aside>

      {leftOpen && <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setLeftOpen(false)} />}

      {deletingChat && (
        <div className="dialog-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !chatActionPending) setDeletingChat(null);
        }}>
          <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-chat-title">
            <div className="confirm-dialog-icon"><Trash2 size={19} /></div>
            <h2 id="delete-chat-title">Delete this chat?</h2>
            <p>
              <strong>{deletingChat.title}</strong> and its messages, saved experiments, and generated artifacts will be permanently removed.
            </p>
            <div className="confirm-dialog-actions">
              <button disabled={chatActionPending} onClick={() => setDeletingChat(null)}>Cancel</button>
              <button className="danger-button" disabled={chatActionPending} onClick={() => void deleteSelectedChat()}>
                {chatActionPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}
                Delete chat
              </button>
            </div>
          </section>
        </div>
      )}

      <section className="conversation-panel">
        <header className="workspace-header">
          <button className="icon-button mobile-menu" onClick={() => setLeftOpen(true)} aria-label="Open navigation"><Menu size={19} /></button>
          <div className="workspace-title">
            <span className="eyebrow">Scientific analysis</span>
            {activeChat && editingChatId === activeChat.id ? (
              <form className="workspace-rename-form" onSubmit={(event) => void renameChat(event, activeChat.id)}>
                <input
                  autoFocus
                  maxLength={120}
                  value={editingTitle}
                  onChange={(event) => setEditingTitle(event.target.value)}
                  aria-label="Analysis name"
                />
                <button type="submit" disabled={!editingTitle.trim() || chatActionPending} aria-label="Save analysis name">
                  <Check size={14} />
                </button>
                <button type="button" onClick={() => setEditingChatId(null)} aria-label="Cancel renaming">
                  <X size={14} />
                </button>
              </form>
            ) : (
              <div className="workspace-title-row">
                <h1>{activeChat?.title ?? "Untitled investigation"}</h1>
                {activeChat && (
                  <button className="title-edit-button" onClick={() => startRenamingChat(activeChat)}>
                    <Pencil size={12} /> Edit name
                  </button>
                )}
              </div>
            )}
          </div>
          <div className="header-actions">
            <div className={`status-pill ${health?.mode === "live" ? "live" : "demo"}`}>
              <span className="status-dot" />
              {health?.mode === "live" ? health.model : "Local demo"}
            </div>
            <button
              className="secondary-button"
              onClick={() => void saveCurrentExperiment()}
              disabled={!activeChat?.messages.length || saving}
            >
              {saving ? <LoaderCircle className="spin" size={15} /> : <Archive size={15} />}
              Save experiment
            </button>
            <button className="icon-button artifacts-toggle" onClick={() => setRightOpen((value) => !value)} aria-label="Toggle artifacts">
              {rightOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}
            </button>
          </div>
        </header>

        {error && (
          <div className="error-banner">
            <CircleAlert size={17} /><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss error"><X size={15} /></button>
          </div>
        )}

        <div className="message-scroll">
          {!activeChat?.messages.length && !activeRunId ? (
            <EmptyState dataset={dataset} onSelect={(prompt) => void sendMessage(prompt)} />
          ) : (
            <div className="message-column">
              {activeChat?.messages.map((message) => (
                <article className={`message ${message.role}`} key={message.id}>
                  {message.role === "assistant" && <div className="assistant-avatar"><Sparkles size={15} /></div>}
                  <div className="message-body">
                    <div className="message-label">{message.role === "user" ? "You" : "Axiom agent"}</div>
                    {message.role === "assistant" ? (
                      <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown></div>
                    ) : <p>{message.content}</p>}
                  </div>
                </article>
              ))}

              {activeRunId && (
                <article className="message assistant live-message">
                  <div className="assistant-avatar active"><Sparkles size={15} /></div>
                  <div className="message-body">
                    <div className="message-label"><span>Axiom agent</span><span className="working-label"><LoaderCircle className="spin" size={12} /> Working</span></div>
                    {(plan || liveSteps.length > 0) && <RunTrace plan={plan} steps={liveSteps} />}
                    {liveText && <div className="markdown live-copy"><ReactMarkdown remarkPlugins={[remarkGfm]}>{liveText}</ReactMarkdown><span className="text-cursor" /></div>}
                  </div>
                </article>
              )}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        <div className="composer-wrap">
          <form className="composer" onSubmit={submit}>
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  if (input.trim()) void sendMessage(input);
                }
              }}
              placeholder="Ask a question about the experimental data…"
              rows={2}
              disabled={Boolean(activeRunId)}
              aria-label="Scientific question"
            />
            <div className="composer-footer">
              <span><Database size={13} /> DuckDB · read-only</span>
              <button type="submit" disabled={!input.trim() || Boolean(activeRunId)} aria-label="Send question"><Send size={16} /></button>
            </div>
          </form>
          <p className="composer-note">Analyses may be incomplete. Review methods and evidence before making decisions.</p>
        </div>
      </section>

      <aside className={`artifact-panel ${rightOpen ? "open" : "closed"}`}>
        <div className="artifact-header">
          <div><span className="eyebrow">Run outputs</span><h2>Artifacts</h2></div>
          <span className="artifact-count">{allArtifacts.length}</span>
        </div>

        <div className="artifact-scroll">
          {allArtifacts.length ? (
            <>
              {allArtifacts.find((artifact) => artifact.kind === "figure") && (
                <FigurePreview artifact={allArtifacts.find((artifact) => artifact.kind === "figure")!} />
              )}
              <div className="artifact-list">
                {allArtifacts.map((artifact) => <ArtifactRow key={artifact.id} artifact={artifact} />)}
              </div>
            </>
          ) : (
            <div className="artifact-empty">
              <div><FileSpreadsheet size={22} /></div>
              <h3>No outputs yet</h3>
              <p>Tables, figures, SQL, and analysis code will collect here as the agent works.</p>
            </div>
          )}
        </div>

        <div className="provenance-card">
          <div><Check size={14} /><strong>Reproducible by default</strong></div>
          <p>Every saved experiment includes the transcript, execution trace, source code, queries, and generated files.</p>
        </div>
      </aside>
    </main>
  );
}


function EmptyState({ dataset, onSelect }: { dataset: DatasetSummary | null; onSelect: (prompt: string) => void }) {
  return (
    <div className="empty-state">
      <div className="empty-kicker"><span /><p>Connected to experimental data</p></div>
      <h2>What would you like to<br />understand?</h2>
      <p className="empty-subtitle">Ask a question in plain language. The agent will plan the analysis, query the data, run code, and show its work.</p>
      <div className="starter-grid">
        {STARTERS.map((starter) => (
          <button key={starter.title} onClick={() => onSelect(starter.prompt)}>
            <div><starter.icon size={18} /></div>
            <span><strong>{starter.title}</strong><small>{starter.prompt}</small></span>
            <ChevronRight size={16} />
          </button>
        ))}
      </div>
      <div className="coverage-row">
        <span><strong>{dataset?.compound_count ?? 33}</strong> compounds</span>
        <span><strong>4</strong> modalities</span>
        <span><strong>{dataset?.tables.length ?? 15}</strong> data tables</span>
      </div>
    </div>
  );
}


function RunTrace({ plan, steps }: { plan: AnalysisPlan | null; steps: Step[] }) {
  return (
    <div className="run-trace">
      <div className="run-trace-head">
        <div><LoaderCircle className="spin" size={15} /><strong>{plan?.title ?? "Planning analysis"}</strong></div>
        <span>{steps.filter((step) => step.status === "completed").length}/{Math.max(plan?.steps.length ?? 0, steps.length)} complete</span>
      </div>
      <div className="run-step-list">
        {(steps.length ? steps : plan?.steps.map((step, index) => ({
          id: `planned-${index}`,
          run_id: "",
          ordinal: index + 1,
          tool_name: "planned",
          title: step.label,
          status: index === 0 ? "running" as const : "queued" as Step["status"],
          started_at: "",
        })) ?? []).map((step) => (
          <div className={`run-step ${step.status}`} key={step.id}>
            <span className="step-icon">{step.status === "completed" ? <Check size={11} /> : step.status === "failed" ? <X size={11} /> : <span />}</span>
            <div><strong>{step.title}</strong>{step.output_summary && <small>{step.output_summary}</small>}</div>
          </div>
        ))}
      </div>
    </div>
  );
}


function FigurePreview({ artifact }: { artifact: Artifact }) {
  return (
    <div className="figure-preview">
      <div className="figure-frame"><Image src={`${API_BASE}${artifact.download_url}`} alt={artifact.name} width={640} height={480} unoptimized /></div>
      <div className="figure-caption"><span><BarChart3 size={14} /> Latest figure</span><strong>{artifact.name.replace(/[-_]/g, " ").replace(/\.png$/i, "")}</strong></div>
    </div>
  );
}


function ArtifactRow({ artifact }: { artifact: Artifact }) {
  const Icon = artifact.kind === "figure" ? BarChart3 : artifact.kind === "table" ? FileSpreadsheet : artifact.kind === "code" ? Code2 : Database;
  return (
    <a className="artifact-row" href={`${API_BASE}${artifact.download_url}`} target="_blank" rel="noreferrer">
      <span className={`artifact-icon ${artifact.kind}`}><Icon size={16} /></span>
      <span className="artifact-info"><strong>{artifact.name}</strong><small>{artifact.kind} · {formatBytes(artifact.size_bytes)}</small></span>
      <ArrowDownToLine size={15} />
    </a>
  );
}
