const statusColors = {
  Submitted: "bg-blue-100 text-blue-800",
  PreSubmitted: "bg-yellow-100 text-yellow-800",
  Filled: "bg-green-100 text-green-800",
  Cancelled: "bg-gray-100 text-gray-600",
  Inactive: "bg-red-100 text-red-800",
};

export default function OrdersTable({ orders }) {
  if (!orders || orders.length === 0) {
    return <p className="text-gray-500 text-sm">No open orders.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            {["Order ID", "Symbol", "Action", "Qty", "Type", "Limit", "Status"].map(
              (h) => (
                <th
                  key={h}
                  className="px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider text-left"
                >
                  {h}
                </th>
              )
            )}
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-gray-100">
          {orders.map((o) => (
            <tr key={o.order_id} className="hover:bg-gray-50">
              <td className="px-4 py-3 text-sm">{o.order_id}</td>
              <td className="px-4 py-3 text-sm font-medium">{o.symbol}</td>
              <td
                className={`px-4 py-3 text-sm font-medium ${
                  o.action === "BUY" ? "text-green-600" : "text-red-600"
                }`}
              >
                {o.action}
              </td>
              <td className="px-4 py-3 text-sm">{o.quantity}</td>
              <td className="px-4 py-3 text-sm">{o.order_type}</td>
              <td className="px-4 py-3 text-sm">
                {o.limit_price != null ? `$${o.limit_price.toFixed(2)}` : "—"}
              </td>
              <td className="px-4 py-3 text-sm">
                <span
                  className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${
                    statusColors[o.status] || "bg-gray-100 text-gray-700"
                  }`}
                >
                  {o.status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
