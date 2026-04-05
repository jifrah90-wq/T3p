const cards = [
  { key: "net_liquidation", label: "Net Liquidation", prefix: "$" },
  { key: "total_cash_balance", label: "Total Cash", prefix: "$" },
  { key: "unrealised_pnl", label: "Unrealised P&L", prefix: "$", colored: true },
  { key: "realised_pnl", label: "Realised P&L", prefix: "$", colored: true },
  { key: "buying_power", label: "Buying Power", prefix: "$" },
];

function fmt(val) {
  if (val == null) return "—";
  return Number(val).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export default function AccountSummary({ data }) {
  if (!data) return null;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-6">
      {cards.map((card) => {
        const val = data[card.key];
        const colorClass =
          card.colored && val != null
            ? val >= 0
              ? "text-green-600"
              : "text-red-600"
            : "text-gray-900";
        return (
          <div
            key={card.key}
            className="bg-white rounded-lg shadow p-4 border border-gray-100"
          >
            <p className="text-xs text-gray-500 uppercase tracking-wide">
              {card.label}
            </p>
            <p className={`text-xl font-semibold mt-1 ${colorClass}`}>
              {card.prefix}
              {fmt(val)}
            </p>
          </div>
        );
      })}
    </div>
  );
}
