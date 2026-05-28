import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

type Sub = {
  id: number;
  trip_purpose: string;
  trip_dates: string;
  status: string;
  created_at: string;
  employee?: { name: string };
};

export default function HistoryPage() {
  const [items, setItems] = useState<Sub[]>([]);
  const [status, setStatus] = useState("");

  useEffect(() => {
    const q = status ? `?status=${status}` : "";
    api<Sub[]>(`/submissions${q}`).then(setItems);
  }, [status]);

  return (
    <div className="card">
      <h2>Submission history</h2>
      <label>Filter by status</label>
      <select value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">All</option>
        <option value="draft">draft</option>
        <option value="processing">processing</option>
        <option value="reviewed">reviewed</option>
      </select>
      <table style={{ width: "100%", marginTop: "1rem" }}>
        <thead>
          <tr>
            <th align="left">ID</th>
            <th align="left">Employee</th>
            <th align="left">Trip</th>
            <th align="left">Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((s) => (
            <tr key={s.id}>
              <td>{s.id}</td>
              <td>{s.employee?.name}</td>
              <td>
                {s.trip_dates}
                <br />
                <small>{s.trip_purpose.slice(0, 60)}…</small>
              </td>
              <td>
                <span className={`badge ${s.status}`}>{s.status}</span>
              </td>
              <td>
                <Link to={`/submissions/${s.id}`}>Open</Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
