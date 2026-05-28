const API = "/api";

const POLL_MS = 3000;
const MAX_POLL_MS = 30 * 60 * 1000;

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

/** Start pre-review in background; poll until reviewed or failed (Cloudflare-safe). */
export async function runSubmissionReview(submissionId: number): Promise<SubmissionDetail> {
  await api<{ submission_id: number; status: string }>(`/submissions/${submissionId}/review`, {
    method: "POST",
  });

  const deadline = Date.now() + MAX_POLL_MS;
  while (Date.now() < deadline) {
    const sub = await api<SubmissionDetail>(`/submissions/${submissionId}`);
    if (sub.status === "reviewed") return sub;
    if (sub.status === "failed") {
      throw new Error("Pre-review failed. Check API logs and try again.");
    }
    await sleep(POLL_MS);
  }
  throw new Error("Pre-review timed out while waiting for results.");
}

export type PolicyChatResult = {
  answer: string;
  refused: boolean;
  retrieval_confidence: number;
  citations: { doc_id: string; section: string; quote: string }[];
};

/** Policy Q&A via background job + poll (Cloudflare-safe). */
export async function askPolicyChat(message: string): Promise<PolicyChatResult> {
  const started = await api<{ job_id: string; status: string }>("/policy/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });

  const deadline = Date.now() + MAX_POLL_MS;
  while (Date.now() < deadline) {
    const job = await api<{
      job_id: string;
      status: string;
      result?: PolicyChatResult;
      error?: string;
    }>(`/policy/chat/jobs/${started.job_id}`);

    if (job.status === "completed" && job.result) return job.result;
    if (job.status === "failed") {
      throw new Error(job.error || "Policy Q&A failed.");
    }
    await sleep(POLL_MS);
  }
  throw new Error("Policy Q&A timed out while waiting for an answer.");
}

export type Employee = {
  id: number;
  employee_id: string;
  name: string;
  grade: number;
  title: string;
  department: string;
};

export type Verdict = {
  status: string;
  reasoning: string;
  policy_doc_id?: string;
  policy_quote?: string;
  confidence: number;
  effective_status?: string;
  agent_results?: Record<string, unknown>;
};

export type LineItem = {
  id: number;
  vendor: string;
  amount: number;
  category: string;
  description: string;
  extraction_confidence: number;
  verdict?: Verdict;
  overrides: { id: number; new_status: string; comment: string; created_at: string }[];
};

export type SubmissionDetail = {
  id: number;
  trip_purpose: string;
  trip_dates: string;
  status: string;
  employee?: Employee;
  receipts: {
    id: number;
    filename: string;
    line_items: LineItem[];
  }[];
};
