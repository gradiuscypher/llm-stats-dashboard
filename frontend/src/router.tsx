import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
} from "@tanstack/react-router";
import { queryClient } from "@/lib/queryClient";
import { authApi, ApiError } from "@/lib/api";
import { LoginPage } from "@/routes/login";
import { RegisterPage } from "@/routes/register";
import { DashboardPage } from "@/routes/dashboard";
import { LogsPage } from "@/routes/logs";
import { LogDetailPage } from "@/routes/log-detail";
import { ConversationPage } from "@/routes/conversation";
import { ApiKeysPage } from "@/routes/api-keys";
import { DocsPage } from "@/routes/docs";

// ─── Auth guard ─────────────────────────────────────────────────────────────

async function requireAuth() {
  try {
    await queryClient.fetchQuery({ queryKey: ["me"], queryFn: authApi.me });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      throw redirect({ to: "/login" });
    }
    throw e;
  }
}

// ─── Routes ─────────────────────────────────────────────────────────────────

const rootRoute = createRootRoute({ component: Outlet });

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
});

const registerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/register",
  component: RegisterPage,
});

const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: requireAuth,
  component: DashboardPage,
});

const logsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/logs",
  beforeLoad: requireAuth,
  component: LogsPage,
  validateSearch: (search: Record<string, unknown>) => ({
    model: typeof search.model === "string" ? search.model : undefined,
    provider: typeof search.provider === "string" ? search.provider : undefined,
    conversation_id: typeof search.conversation_id === "string" ? search.conversation_id : undefined,
  }),
});

const logDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/logs/$logId",
  beforeLoad: requireAuth,
  component: ({ useParams }) => {
    const { logId } = useParams();
    return <LogDetailPage logId={logId} />;
  },
});

const conversationRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/conversations/$conversationId",
  beforeLoad: requireAuth,
  component: ({ useParams }) => {
    const { conversationId } = useParams();
    return <ConversationPage conversationId={conversationId} />;
  },
});

const apiKeysRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/api-keys",
  beforeLoad: requireAuth,
  component: ApiKeysPage,
});

const docsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/docs",
  // No auth guard — docs are publicly accessible so a coding agent
  // can reach them with only a base URL (no login required).
  component: DocsPage,
});

// ─── Router ─────────────────────────────────────────────────────────────────

const routeTree = rootRoute.addChildren([
  loginRoute,
  registerRoute,
  dashboardRoute,
  logsRoute,
  logDetailRoute,
  conversationRoute,
  apiKeysRoute,
  docsRoute,
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
