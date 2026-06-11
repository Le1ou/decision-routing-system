import { useEffect, useRef } from "react";

export function usePolling(callback: () => Promise<void>, intervalMs: number, enabled: boolean) {
  const callbackRef = useRef(callback);
  const isRunningRef = useRef(false);

  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  useEffect(() => {
    if (!enabled) {
      return undefined;
    }

    const run = async () => {
      if (isRunningRef.current || document.visibilityState === "hidden") {
        return;
      }

      isRunningRef.current = true;

      try {
        await callbackRef.current();
      } catch {
        console.warn("Не удалось выполнить фоновое обновление.");
      } finally {
        isRunningRef.current = false;
      }
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void run();
      }
    };

    const intervalId = window.setInterval(() => {
      void run();
    }, intervalMs);

    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [enabled, intervalMs]);
}
