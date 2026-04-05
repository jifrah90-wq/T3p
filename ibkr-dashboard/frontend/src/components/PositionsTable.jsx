const columns = [
  { key: "symbol", label: "Symbol" },
  { key: "exchange", label: "Exchange" },
  { key: "currency", label: "Currency" },
  { key: "position_size", label: "Position", numeric: true },
  { key: "average_cost", label: "Avg Cost", numeric: true, prefix: "$" },
  { key: "market_value", label: "Market Value", numeric: true, prefix: "$" },
  { key: "unrealised_pnl", label: "Unrealised P&L", numeric: true, prefix: "$", colored: true },
];

function fmt(val) {
  if (val == null) return "—";
  return Number(val).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export default function PositionsTable({ positions }) {
  if (!positions || positions.length === 0) {
    return <p className="text-gray-500 text-sm">No open positions.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                className={`px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider ${
                  col.numeric ? "text-right" : "text-left"
                }`}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-gray-100">
          {positions.map((pos, idx) => (
            <tr key={idx} className="hover:bg-gray-50">
              {columns.map((col) => {
                const val = pos[col.key];
                const colorClass =
                  col.colored && val != null
                    ? val >= 0
                      ? "text-green-600"
                      : "text-red-600"
                    : "";
                return (
                  <td
                    key={col.key}
                    className={`px-4 py-3 text-sm whitespace-nowrap ${
                      col.numeric ? "text-right" : ""
                    } ${colorClass}`}
                  >
                    {col.numeric ? `${col.prefix || ""}${fmt(val)}` : val || "—"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
