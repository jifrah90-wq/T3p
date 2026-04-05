import { usePolling } from "../hooks/usePolling";
import AccountSummary from "../components/AccountSummary";
import PositionsTable from "../components/PositionsTable";
import ExportButton from "../components/ExportButton";

export default function Dashboard() {
  const { data: account, loading: accLoading, error: accError } = usePolling("/account/summary");
  const { data: positions, loading: posLoading, error: posError } = usePolling("/positions");

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-4">Account Overview</h1>

      {accError && (
        <div className="bg-red-50 border border-red-200 rounded-md p-3 mb-4 text-sm text-red-700">
          {accError}
        </div>
      )}

      {accLoading && !account ? (
        <p className="text-gray-400 text-sm mb-4">Loading account data...</p>
      ) : (
        <AccountSummary data={account} />
      )}

      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold text-gray-800">Positions</h2>
        <ExportButton dataset="positions" />
      </div>

      {posError && (
        <div className="bg-red-50 border border-red-200 rounded-md p-3 mb-4 text-sm text-red-700">
          {posError}
        </div>
      )}

      {posLoading && !positions ? (
        <p className="text-gray-400 text-sm">Loading positions...</p>
      ) : (
        <div className="bg-white rounded-lg shadow border border-gray-100">
          <PositionsTable positions={positions?.positions} />
        </div>
      )}
    </div>
  );
}
