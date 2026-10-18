export type Health = {
  status: string;
  data_ready: boolean;
  mode: "demo" | "live";
  model: string;
};

export type DatasetSummary = {
  name: string;
  compound_count: number;
  generated_at: string;
  anonymization: string;
  tables: Array<{
    table_name: string;
    modality: string;
    grain: string;
    row_count: number;
    compound_count: number | null;
  }>;
  modalities: Array<{
    name: string;
    row_count: number;
    compound_count: number | null;
  }>;
};

export type Message = {
  id: string;
  chat_id: string;
  run_id: string | null;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};

export type Run = {
  id: string;
  chat_id: string;
  status: "queued" | "running" | "completed" | "failed";
  model: string;
  error: string | null;
  created_at: string;
};

export type Step = {
  id: string;
  run_id: string;
  ordinal: number;
  tool_name: string;
  title: string;
  status: "queued" | "running" | "completed" | "failed";
  input?: Record<string, unknown>;
  output_summary?: string;
  started_at: string;
};

export type Artifact = {
  id: string;
  run_id: string;
  chat_id: string;
  name: string;
  kind: "figure" | "table" | "code" | "report" | "data" | "file";
  mime_type: string;
  size_bytes: number;
  download_url: string;
  created_at: string;
};

export type ChatSummary = {
  id: string;
  title: string;
  preview: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
};

export type ChatDetail = ChatSummary & {
  messages: Message[];
  runs: Run[];
  steps: Step[];
  artifacts: Artifact[];
};

export type Experiment = {
  id: string;
  chat_id: string;
  name: string;
  description: string;
  chat_title: string;
  artifact_count: number;
  created_at: string;
  updated_at: string;
};

export type AnalysisPlan = {
  title: string;
  steps: Array<{ label: string }>;
};
