import { usePolling } from "../hooks/usePolling";
import OrdersTable from "../components/OrdersTable";
import ExportButton from "../components/ExportButton";

export default function Orders() {
  const { data, loading, error } = usePolling("/orders");

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-bold text-gray-900">Open Orders</h1>
        <ExportButton dataset="orders" />
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-md p-3 mb-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {loading && !data ? (
        <p className="text-gray-400 text-sm">Loading orders...</p>
      ) : (
        <div className="bg-white rounded-lg shadow border border-gray-100">
          <OrdersTable orders={data?.orders} />
        </div>
      )}
    </div>
  );
}
