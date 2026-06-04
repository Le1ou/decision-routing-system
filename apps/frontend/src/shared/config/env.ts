function resolveApiUrl(configuredUrl?: string) {
  const browserHost = window.location.hostname;
  const fallbackUrl = `${window.location.protocol}//${browserHost}:3000`;

  if (!configuredUrl?.trim()) {
    return fallbackUrl;
  }

  const url = new URL(configuredUrl);
  const configuredForLocalhost = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  const openedRemotely = browserHost !== "localhost" && browserHost !== "127.0.0.1";

  // A remote browser resolves localhost to the user's machine, not the server
  // hosting the frontend. Keep the configured backend port but use the page host.
  if (configuredForLocalhost && openedRemotely) {
    url.hostname = browserHost;
  }

  return url.toString().replace(/\/$/, "");
}

export const env = {
  apiUrl: resolveApiUrl(import.meta.env.VITE_API_URL),
};
