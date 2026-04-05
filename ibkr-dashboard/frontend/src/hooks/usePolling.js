import { useState, useEffect, useCallback, useRef } from "react";

const API_BASE = "/api";

export function usePolling(endpoint, intervalMs = 10000) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const mountedRef = useRef(true);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}${endpoint}`);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }
      const json = await res.json();
      if (mountedRef.current) {
        setData(json);
        setError(null);
      }
    } catch (err) {
      if (mountedRef.current) {
        setError(err.message);
      }
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, [endpoint]);

  useEffect(() => {
    mountedRef.current = true;
    setLoading(true);
    fetchData();
    const id = setInterval(fetchData, intervalMs);
    return () => {
      mountedRef.current = false;
      clearInterval(id);
    };
  }, [fetchData, intervalMs]);

  return { data, loading, error, refetch: fetchData };
}

export function useHealthCheck(intervalMs = 5000) {
  const [connected, setConnected] = useState(null);
  const [healthData, setHealthData] = useState(null);

  useEffect(() => {
    let mounted = true;
    const check = async () => {
      try {
        const res = await fetch(`${API_BASE}/health`);
        const json = await res.json();
        if (mounted) {
          setConnected(json.connected);
          setHealthData(json);
        }
      } catch {
        if (mounted) {
          setConnected(false);
          setHealthData(null);
        }
      }
    };
    check();
    const id = setInterval(check, intervalMs);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, [intervalMs]);

  return { connected, healthData };
}
