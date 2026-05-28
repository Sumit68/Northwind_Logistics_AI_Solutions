import { useState } from "react";
import { askPolicyChat, PolicyChatResult } from "../api";
import LoaderButton from "../components/LoaderButton";

export default function PolicyChatPage() {
  const [message, setMessage] = useState("What is the dinner cap for solo travel?");
  const [result, setResult] = useState<PolicyChatResult | null>(null);
  const [busy, setBusy] = useState(false);

  async function ask() {
    setBusy(true);
    try {
      const r = await askPolicyChat(message);
      setResult(r);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>Policy Q&amp;A</h2>
      <p>Grounded answers from the travel &amp; expense policy library. Out-of-scope questions are refused.</p>
      <textarea rows={3} value={message} onChange={(e) => setMessage(e.target.value)} />
      <LoaderButton loading={busy} disabled={!message.trim() || busy} onClick={ask}>
        Ask policy librarian
      </LoaderButton>
      {busy && <p className="meta-row">Searching policy library and generating answer…</p>}
      {result && (
        <div style={{ marginTop: "1rem" }}>
          {result.refused ? (
            <p>
              <strong>Refused</strong> (confidence {(result.retrieval_confidence * 100).toFixed(0)}%)
            </p>
          ) : (
            <p>
              <strong>Answer</strong> (retrieval {(result.retrieval_confidence * 100).toFixed(0)}%)
            </p>
          )}
          <p>{result.answer}</p>
          {result.citations?.length > 0 && (
            <>
              <h4>Citations</h4>
              <ul>
                {result.citations.map((c, i) => (
                  <li key={i}>
                    {c.doc_id} {c.section}: &ldquo;{c.quote}&rdquo;
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
