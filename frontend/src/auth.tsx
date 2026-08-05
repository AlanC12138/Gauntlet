import { ClerkProvider, Show, SignInButton, UserButton, useAuth } from "@clerk/react";
import { createContext, useContext, type ReactNode } from "react";

/**
 * Auth that can be absent.
 *
 * The backend runs with auth disabled when no Clerk secret is configured, and
 * the frontend has to match: a build with no publishable key must render no
 * sign-in affordance at all, rather than a button that cannot work.
 *
 * The awkward part is that Clerk's hooks throw outside a `ClerkProvider`, so
 * components cannot simply call `useAuth()` and cope. Instead the provider is
 * chosen once at the top of the tree and both branches publish the same
 * context, so every consumer calls one hook unconditionally and never knows
 * which mode it is in.
 */

const PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;

/** Build-time constant: whether this bundle was compiled with a Clerk key. */
export const authConfigured = Boolean(PUBLISHABLE_KEY);

interface TokenSource {
  /** Bearer token for the API, or null when auth is disabled or signed out. */
  getToken: () => Promise<string | null>;
}

const TokenContext = createContext<TokenSource>({ getToken: async () => null });

export function useToken(): TokenSource {
  return useContext(TokenContext);
}

/** Publishes Clerk's token getter into the shared context. */
function ClerkTokenBridge({ children }: { children: ReactNode }) {
  const { getToken } = useAuth();
  return (
    <TokenContext.Provider value={{ getToken: () => getToken() }}>
      {children}
    </TokenContext.Provider>
  );
}

export function AuthProvider({ children }: { children: ReactNode }) {
  if (!PUBLISHABLE_KEY) return <>{children}</>;

  return (
    <ClerkProvider publishableKey={PUBLISHABLE_KEY} afterSignOutUrl="/">
      <ClerkTokenBridge>{children}</ClerkTokenBridge>
    </ClerkProvider>
  );
}

/** Sign-in button or avatar menu. Renders nothing when auth is not configured. */
export function AuthControls() {
  if (!authConfigured) return null;

  return (
    <div className="auth-controls">
      <Show when="signed-out">
        <SignInButton mode="modal">
          <button className="secondary">Sign in</button>
        </SignInButton>
      </Show>
      <Show when="signed-in">
        <UserButton />
      </Show>
    </div>
  );
}

/** Renders `children` only when the visitor may start a run. */
export function WhenCanRun({
  children,
  fallback,
}: {
  children: ReactNode;
  fallback: ReactNode;
}) {
  if (!authConfigured) return <>{children}</>;
  return (
    <>
      <Show when="signed-in">{children}</Show>
      <Show when="signed-out">{fallback}</Show>
    </>
  );
}
