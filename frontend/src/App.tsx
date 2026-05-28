import { Link, Route, Routes } from "react-router-dom";
import EmployeesPage from "./pages/EmployeesPage";
import HistoryPage from "./pages/HistoryPage";
import NewSubmissionPage from "./pages/NewSubmissionPage";
import PolicyChatPage from "./pages/PolicyChatPage";
import SubmissionDetailPage from "./pages/SubmissionDetailPage";

export default function App() {
  return (
    <div className="app-shell">
      <header>
        <h1>Northwind Expense Pre-Review</h1>
        <nav>
          <Link to="/">New submission</Link>
          <Link to="/history">History</Link>
          <Link to="/employees">Employees</Link>
          <Link to="/policy">Policy Q&amp;A</Link>
        </nav>
      </header>
      <Routes>
        <Route path="/" element={<NewSubmissionPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/employees" element={<EmployeesPage />} />
        <Route path="/policy" element={<PolicyChatPage />} />
        <Route path="/submissions/:id" element={<SubmissionDetailPage />} />
      </Routes>
    </div>
  );
}
