import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Employee, runSubmissionReview } from "../api";
import LoaderButton from "../components/LoaderButton";

function formatTripDates(start: string, end: string) {
  if (!start) return "";
  if (!end || end === start) return start;
  return `${start} to ${end}`;
}

export default function NewSubmissionPage() {
  const nav = useNavigate();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [employeeId, setEmployeeId] = useState("");
  const [tripPurpose, setTripPurpose] = useState("");
  const [tripStart, setTripStart] = useState("");
  const [tripEnd, setTripEnd] = useState("");
  const [submissionId, setSubmissionId] = useState<number | null>(null);
  const [files, setFiles] = useState<FileList | null>(null);
  const [busy, setBusy] = useState(false);
  const [busyAction, setBusyAction] = useState<"create" | "review" | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Employee[]>("/employees").then(setEmployees).catch((e) => setError(String(e)));
  }, []);

  const tripDates = formatTripDates(tripStart, tripEnd);

  async function createSubmission() {
    setError("");
    setBusy(true);
    setBusyAction("create");
    try {
      const sub = await api<{ id: number }>("/submissions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          employee_id: Number(employeeId),
          trip_purpose: tripPurpose,
          trip_dates: tripDates,
        }),
      });
      setSubmissionId(sub.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  }

  async function uploadAndReview() {
    if (!submissionId || !files?.length) return;
    setBusy(true);
    setBusyAction("review");
    setError("");
    try {
      const fd = new FormData();
      Array.from(files).forEach((f) => fd.append("files", f));
      await fetch(`/api/submissions/${submissionId}/receipts`, { method: "POST", body: fd });
      await runSubmissionReview(submissionId);
      nav(`/submissions/${submissionId}`);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  }

  return (
    <div>
      <div className="card">
        <h2>New expense submission</h2>
        <p className="meta-row">Select employee, trip dates, then upload receipts for AI pre-review.</p>

        <label htmlFor="employee">Employee</label>
        <select id="employee" value={employeeId} onChange={(e) => setEmployeeId(e.target.value)}>
          <option value="">Select employee…</option>
          {employees.map((e) => (
            <option key={e.id} value={e.id}>
              {e.name} ({e.employee_id}) — {e.title}
            </option>
          ))}
        </select>

        <label htmlFor="purpose">Trip purpose</label>
        <textarea
          id="purpose"
          rows={2}
          value={tripPurpose}
          onChange={(e) => setTripPurpose(e.target.value)}
          placeholder="Business purpose of the trip"
        />

        <label>Trip dates</label>
        <div className="date-range">
          <div>
            <label htmlFor="trip-start" style={{ textTransform: "none", fontSize: "0.85rem" }}>
              Start
            </label>
            <input
              id="trip-start"
              type="date"
              value={tripStart}
              onChange={(e) => {
                setTripStart(e.target.value);
                if (!tripEnd || tripEnd < e.target.value) setTripEnd(e.target.value);
              }}
            />
          </div>
          <div>
            <label htmlFor="trip-end" style={{ textTransform: "none", fontSize: "0.85rem" }}>
              End
            </label>
            <input
              id="trip-end"
              type="date"
              min={tripStart || undefined}
              value={tripEnd}
              onChange={(e) => setTripEnd(e.target.value)}
            />
          </div>
        </div>

        <LoaderButton
          loading={busyAction === "create"}
          disabled={busy || !employeeId || !tripPurpose || !tripStart}
          onClick={createSubmission}
        >
          Create submission
        </LoaderButton>
        {submissionId && (
          <p className="meta-row" style={{ marginTop: "0.75rem" }}>
            Submission <strong>#{submissionId}</strong> created.
          </p>
        )}
      </div>

      {submissionId && (
        <div className="card">
          <h2>Upload receipts</h2>
          <p className="meta-row">PDF, JPG, PNG, or TXT — one line item per receipt.</p>
          <input
            type="file"
            multiple
            accept=".pdf,.png,.jpg,.jpeg,.txt"
            onChange={(e) => setFiles(e.target.files)}
          />
          <LoaderButton
            loading={busyAction === "review"}
            disabled={busy || !files?.length}
            onClick={uploadAndReview}
          >
            Upload &amp; run pre-review
          </LoaderButton>
        </div>
      )}
      {error && <div className="alert-error">{error}</div>}
    </div>
  );
}
