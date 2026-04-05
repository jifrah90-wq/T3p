import { Routes, Route, NavLink } from "react-router-dom";
import { useHealthCheck } from "./hooks/usePolling";
import Dashboard from "./pages/Dashboard";
import Orders from "./pages/Orders";
import MarketData from "./pages/MarketData";

const navLinks = [
  { to: "/", label: "Dashboard" },
  { to: "/orders", label: "Orders" },
  { to: "/market-data", label: "Market Data" },
];

export default function App() {
  const { connected } = useHealthCheck();

  return (
    <div className="min-h-screen flex flex-col">
      {connected === false && (
        <div className="bg-red-600 text-white text-center py-2 text-sm font-medium">
          Disconnected from IB Gateway / TWS — data may be stale
        </div>
      )}

      <nav className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-14">
            <span className="text-lg font-bold text-gray-900">
              IBKR Dashboard
            </span>
            <div className="flex space-x-4">
              {navLinks.map((link) => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  end={link.to === "/"}
                  className={({ isActive }) =>
                    `px-3 py-2 rounded-md text-sm font-medium ${
                      isActive
                        ? "bg-indigo-100 text-indigo-700"
                        : "text-gray-600 hover:text-gray-900 hover:bg-gray-100"
                    }`
                  }
                >
                  {link.label}
                </NavLink>
              ))}
            </div>
            <div className="flex items-center space-x-2">
              <span
                className={`inline-block w-2.5 h-2.5 rounded-full ${
                  connected ? "bg-green-500" : "bg-red-500"
                }`}
              />
              <span className="text-xs text-gray-500">
                {connected === null
                  ? "Checking..."
                  : connected
                  ? "Connected"
                  : "Disconnected"}
              </span>
            </div>
          </div>
        </div>
      </nav>

      <main className="flex-1 max-w-7xl mx-auto w-full px-4 sm:px-6 lg:px-8 py-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/orders" element={<Orders />} />
          <Route path="/market-data" element={<MarketData />} />
        </Routes>
      </main>
    </div>
  );
}
