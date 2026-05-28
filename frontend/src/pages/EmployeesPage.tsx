import { useEffect, useState } from "react";
import { api, Employee } from "../api";

export default function EmployeesPage() {
  const [employees, setEmployees] = useState<Employee[]>([]);

  useEffect(() => {
    api<Employee[]>("/employees").then(setEmployees);
  }, []);

  return (
    <div className="card">
      <h2>Employees (seeded)</h2>
      <p>Loaded from sample <code>employee_info.json</code> files at startup.</p>
      <ul>
        {employees.map((e) => (
          <li key={e.id}>
            <strong>{e.name}</strong> ({e.employee_id}) — Grade {e.grade}, {e.title}
          </li>
        ))}
      </ul>
    </div>
  );
}
