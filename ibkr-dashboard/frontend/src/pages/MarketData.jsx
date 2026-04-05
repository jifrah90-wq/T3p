import { useState } from "react";
import SnapshotCard from "../components/SnapshotCard";

export default function MarketData() {
  const [symbol, setSymbol] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleFetch = async (e) => {
    e.preventDefault();
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;

    setLoading(true);
    setError(null);
    setData(null);

    try {
      const res = await fetch(`/api/market-data/${sym}`);
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `HTTP ${res.status}`);
      }
      setData(await res.json());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-4">Market Data</h1>

      <form onSubmit={handleFetch} className="flex items-center space-x-3 mb-6">
        <input
          type="text"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="Enter symbol (e.g. AAPL)"
          className="border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 w-60"
        />
        <button
          type="submit"
          disabled={loading}
          className="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-md hover:bg-indigo-700 disabled:opacity-50"
        >
          {loading ? "Loading..." : "Fetch"}
        </button>
      </form>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-md p-3 mb-4 text-sm text-red-700">
          {error}
        </div>
      )}

      <SnapshotCard data={data} />
    </div>
  );
}
