// The browser only talks to its own origin; Next.js forwards /api/* to FastAPI (ADR-003).

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly retryAfterSeconds?: number,
  ) {
    super(message);
  }
}

interface ValidationIssue {
  msg?: string;
  loc?: (string | number)[];
}

function messageFrom(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      // FastAPI validation errors: [{loc: ["body", "url"], msg: "..."}]
      return (detail as ValidationIssue[])
        .map((issue) => {
          const field = issue.loc?.filter((part) => part !== "body").join(".");
          const text = (issue.msg ?? "is invalid").replace(/^Value error, /, "");
          return field ? `${field}: ${text}` : text;
        })
        .join("; ");
    }
  }
  return `Something went wrong (HTTP ${status}).`;
}

export async function api<T>(
  path: string,
  options: { method?: "GET" | "POST" | "PATCH" | "DELETE"; body?: unknown } = {},
): Promise<T> {
  const hasBody = options.body !== undefined;
  const response = await fetch(`/api${path}`, {
    method: options.method ?? "GET",
    headers: {
      // Required on every mutating request: the API's CSRF defense (ADR-014).
      "X-Requested-With": "tag-monitor",
      ...(hasBody ? { "Content-Type": "application/json" } : {}),
    },
    body: hasBody ? JSON.stringify(options.body) : undefined,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (response.status === 204) return undefined as T;
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const retryAfter = Number(response.headers.get("retry-after")) || undefined;
    throw new ApiError(response.status, messageFrom(body, response.status), retryAfter);
  }
  return body as T;
}
