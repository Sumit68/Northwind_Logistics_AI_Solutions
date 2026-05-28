import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, SubmissionDetail } from "../api";
import LoaderButton from "../components/LoaderButton";

function statusClass(s: string) {
  return s?.replace(" ", "_") || "needs_review";
}

export default function SubmissionDetailPage() {
  const { id } = useParams();
  const [sub, setSub] = useState<SubmissionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [overrideFor, setOverrideFor] = useState<number | null>(null);
  const [newStatus, setNewStatus] = useState("compliant");
  const [comment, setComment] = useState("");
  const [overrideLoading, setOverrideLoading] = useState(false);
  const [reviewLoading, setReviewLoading] = useState(false);

  const load = () => {
    setLoading(true);
    api<SubmissionDetail>(`/submissions/${id}`)
      .then(setSub)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(load, [id]);

  async function rerunReview() {
    setReviewLoading(true);
    setError("");
    try {
      await api(`/submissions/${id}/review`, { method: "POST" });
      load();
    } catch (e) {
      setError(String(e));
      setReviewLoading(false);
    }
  }

  async function submitOverride(lineItemId: number) {
    setOverrideLoading(true);
    try {
      await api(`/submissions/line-items/${lineItemId}/override`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_status: newStatus, comment }),
      });
      setOverrideFor(null);
      setComment("");
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setOverrideLoading(false);
    }
  }

  if (loading && !sub) return <p className="page-loading">Loading submission…</p>;
  if (!sub) return <p className="page-loading">Submission not found.</p>;

  const allItems = sub.receipts.flatMap((r) => r.line_items);
  const statusCounts = allItems.reduce(
    (acc, li) => {
      const s = li.verdict?.effective_status || li.verdict?.status || "needs_review";
      acc[s] = (acc[s] || 0) + 1;
      return acc;
    },
    {} as Record<string, number>
  );
  const tripStatus =
    (statusCounts.rejected || 0) > 0
      ? "rejected"
      : (statusCounts.flagged || 0) > 0
      ? "flagged"
      : (statusCounts.needs_review || 0) > 0
      ? "needs_review"
      : "compliant";

  const sortedReceipts = [...sub.receipts].sort((a, b) => {
    const rank = (receipt: SubmissionDetail["receipts"][number]) => {
      const statuses = receipt.line_items.map(
        (li) => li.verdict?.effective_status || li.verdict?.status || "needs_review"
      );
      if (statuses.includes("rejected")) return 0;
      if (statuses.includes("flagged")) return 1;
      if (statuses.includes("needs_review")) return 2;
      return 3;
    };
    return rank(a) - rank(b);
  });

  return (
    <div>
      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "1rem" }}>
          <div>
            <h2>Submission #{sub.id}</h2>
            <p>
              <strong>{sub.employee?.name}</strong>
              {sub.employee && (
                <span className="meta-row">
                  {" "}
                  — Grade {sub.employee.grade}, {sub.employee.title} ({sub.employee.employee_id})
                </span>
              )}
            </p>
            <p className="meta-row">{sub.trip_purpose}</p>
            <p className="meta-row">
              <span>Trip dates: {sub.trip_dates}</span>
            </p>
          </div>
          <span className={`badge ${statusClass(tripStatus)}`}>trip: {tripStatus}</span>
        </div>
        <p className="meta-row">
          Receipt verdicts — compliant: {statusCounts.compliant || 0}, flagged: {statusCounts.flagged || 0},
          rejected: {statusCounts.rejected || 0}, needs_review: {statusCounts.needs_review || 0}
        </p>
        <LoaderButton loading={reviewLoading} variant="secondary" onClick={rerunReview}>
          Re-run pre-review
        </LoaderButton>
      </div>

      {sortedReceipts.map((r) => (
        <div key={r.id} className="card">
          <h3>{r.filename}</h3>
          {r.line_items.length === 0 && (
            <p className="meta-row">No line items yet — upload receipts and run pre-review.</p>
          )}
          {r.line_items.map((li) => {
            const eff = li.verdict?.effective_status || li.verdict?.status || "needs_review";
            return (
              <div key={li.id} className={`line-item ${statusClass(eff)}`}>
                <div className="line-item-header">
                  <div>
                    <strong>{li.vendor}</strong>
                    <span className="meta-row">
                      {" "}
                      ${li.amount.toFixed(2)} ({li.category})
                    </span>
                  </div>
                  <span className={`badge ${statusClass(eff)}`}>{eff.replace("_", " ")}</span>
                </div>
                {li.verdict && (
                  <>
                    <p>{li.verdict.reasoning}</p>
                    {li.verdict.policy_doc_id && li.verdict.policy_quote && (
                      <blockquote className="policy-quote">
                        {li.verdict.policy_doc_id}: &ldquo;{li.verdict.policy_quote}&rdquo;
                      </blockquote>
                    )}
                    <p className="confidence">
                      Confidence: {(li.verdict.confidence * 100).toFixed(0)}%
                    </p>
                    <div className="confidence-bar">
                      <span style={{ width: `${Math.min(100, li.verdict.confidence * 100)}%` }} />
                    </div>
                  </>
                )}
                {li.overrides.length > 0 && (
                  <div>
                    <strong>Overrides</strong>
                    <ul>
                      {li.overrides.map((o) => (
                        <li key={o.id}>
                          {o.new_status}: {o.comment}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <LoaderButton variant="secondary" onClick={() => setOverrideFor(li.id)}>
                  Override verdict
                </LoaderButton>
                {overrideFor === li.id && (
                  <div style={{ marginTop: "0.75rem" }}>
                    <label>New status</label>
                    <select value={newStatus} onChange={(e) => setNewStatus(e.target.value)}>
                      <option value="compliant">compliant</option>
                      <option value="flagged">flagged</option>
                      <option value="rejected">rejected</option>
                      <option value="needs_review">needs_review</option>
                    </select>
                    <label>Reviewer comment</label>
                    <textarea
                      rows={2}
                      placeholder="Required — explain your override"
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                    />
                    <LoaderButton
                      loading={overrideLoading}
                      disabled={!comment.trim()}
                      onClick={() => submitOverride(li.id)}
                    >
                      Save override
                    </LoaderButton>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}
      {error && <div className="alert-error">{error}</div>}
    </div>
  );
}
