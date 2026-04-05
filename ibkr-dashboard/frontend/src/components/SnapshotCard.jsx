function Row({ label, value, prefix = "" }) {
  if (value == null) return null;
  return (
    <div className="flex justify-between py-1">
      <span className="text-gray-500 text-sm">{label}</span>
      <span className="text-sm font-medium">
        {prefix}
        {Number(value).toLocaleString("en-US", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })}
      </span>
    </div>
  );
}

export default function SnapshotCard({ data }) {
  if (!data) return null;

  return (
    <div className="bg-white rounded-lg shadow border border-gray-100 p-6 max-w-md">
      <h3 className="text-lg font-bold text-gray-900 mb-4">{data.symbol}</h3>
      <div className="divide-y divide-gray-100">
        <Row label="Last Price" value={data.last_price} prefix="$" />
        <Row label="Bid" value={data.bid} prefix="$" />
        <Row label="Ask" value={data.ask} prefix="$" />
        <Row label="Volume" value={data.volume} />
        <Row label="High" value={data.high} prefix="$" />
        <Row label="Low" value={data.low} prefix="$" />
        <Row label="Close" value={data.close} prefix="$" />
      </div>
    </div>
  );
}
