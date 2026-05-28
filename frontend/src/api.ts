const API = "/api";

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
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
