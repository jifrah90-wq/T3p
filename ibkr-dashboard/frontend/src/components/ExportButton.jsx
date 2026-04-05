export default function ExportButton({ dataset, label = "Download CSV" }) {
  const handleExport = () => {
    window.open(`/api/export/${dataset}?fmt=csv`, "_blank");
  };

  return (
    <button
      onClick={handleExport}
      className="inline-flex items-center px-3 py-1.5 border border-gray-300 text-xs font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500"
    >
      {label}
    </button>
  );
}
