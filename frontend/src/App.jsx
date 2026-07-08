import { Navigate, Route, Routes } from 'react-router-dom';
import { useAuth } from './context/AuthContext';
import CustomerView from './pages/CustomerView';
import AdminLogin from './pages/AdminLogin';
import AdminDashboard from './pages/AdminDashboard';
import InventoryPanel from './admin/InventoryPanel';
import PosPanel from './admin/PosPanel';
import SalesEntryPanel from './admin/SalesEntryPanel';
import SummaryPanel from './admin/SummaryPanel';
import DailyHistoryPanel from './admin/DailyHistoryPanel';

function RequireAuth({ children }) {
  const { status } = useAuth();
  if (status === 'checking') {
    return <p className="page-status">Checking session…</p>;
  }
  if (status !== 'authenticated') {
    return <Navigate to="/admin/login" replace />;
  }
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<CustomerView />} />
      <Route path="/admin/login" element={<AdminLogin />} />
      <Route
        path="/admin"
        element={
          <RequireAuth>
            <AdminDashboard />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="inventory" replace />} />
        <Route path="pos" element={<PosPanel />} />
        <Route path="inventory" element={<InventoryPanel />} />
        <Route path="sale" element={<SalesEntryPanel />} />
        <Route path="summary" element={<SummaryPanel />} />
        <Route path="history" element={<DailyHistoryPanel />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
