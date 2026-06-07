import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiKeysApi, ApiKeyCreatedResponse, ApiKeyPublic, ApiError } from "@/lib/api";
import { Layout } from "@/components/Layout";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/Button";
import { Field } from "@/components/Field";

const SCOPE_OPTIONS = ["logs:write", "logs:read", "proxy:use"];

function CreatedKeyBanner({ raw_key, onDismiss }: { raw_key: string; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="mb-6 border border-[var(--color-accent)] bg-[var(--color-surface)] p-4">
      <p className="text-xs font-bold uppercase tracking-wider text-[var(--color-accent)] mb-2">
        ⚠ Copy your API key — it will not be shown again
      </p>
      <div className="flex items-center gap-2 mb-3">
        <code className="flex-1 text-sm bg-[var(--color-code-bg)] px-2 py-1.5 overflow-x-auto">
          {raw_key}
        </code>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            void navigator.clipboard.writeText(raw_key);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          }}
        >
          {copied ? "Copied!" : "Copy"}
        </Button>
      </div>
      <Button variant="ghost" size="sm" onClick={onDismiss}>
        I've saved my key, dismiss
      </Button>
    </div>
  );
}

function CreateKeyForm({ onCreated }: { onCreated: (key: ApiKeyCreatedResponse) => void }) {
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["logs:write"]);
  const [error, setError] = useState<string | null>(null);

  const qc = useQueryClient();
  const createMutation = useMutation({
    mutationFn: () => apiKeysApi.create({ name, scopes }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["api-keys"] });
      onCreated(data);
      setName("");
      setScopes(["logs:write"]);
    },
    onError: (err) => setError(err instanceof ApiError ? err.detail : "Failed to create key"),
  });

  const toggleScope = (scope: string) => {
    setScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]
    );
  };

  return (
    <div className="border border-[var(--color-border)] p-4 bg-[var(--color-surface)] mb-6">
      <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-4">
        New API Key
      </h2>
      <div className="flex flex-col gap-3 max-w-sm">
        <Field
          label="Name"
          placeholder="e.g. laptop, ci-server"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        <div>
          <p className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
            Scopes
          </p>
          <div className="flex gap-3">
            {SCOPE_OPTIONS.map((scope) => (
              <label key={scope} className="flex items-center gap-1.5 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  checked={scopes.includes(scope)}
                  onChange={() => toggleScope(scope)}
                  className="cursor-pointer"
                />
                <code>{scope}</code>
              </label>
            ))}
          </div>
        </div>
        {error && (
          <p className="text-xs text-[var(--color-danger)]">{error}</p>
        )}
        <Button
          onClick={() => { setError(null); createMutation.mutate(); }}
          loading={createMutation.isPending}
          disabled={!name || scopes.length === 0}
          size="sm"
        >
          Create key
        </Button>
      </div>
    </div>
  );
}

export function ApiKeysPage() {
  const [newKey, setNewKey] = useState<ApiKeyCreatedResponse | null>(null);
  const qc = useQueryClient();

  const { data: keys = [], isLoading } = useQuery({
    queryKey: ["api-keys"],
    queryFn: apiKeysApi.list,
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => apiKeysApi.revoke(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys"] }),
  });

  return (
    <Layout>
      <PageHeader
        title="API Keys"
        subtitle="Manage keys for programmatic access"
      />

      {newKey && (
        <CreatedKeyBanner raw_key={newKey.raw_key} onDismiss={() => setNewKey(null)} />
      )}

      <CreateKeyForm onCreated={setNewKey} />

      {isLoading ? (
        <p className="text-sm muted">Loading...</p>
      ) : (
        <div className="border border-[var(--color-border)] bg-[var(--color-surface)]">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Prefix</th>
                <th>Scopes</th>
                <th>Last used</th>
                <th>Created</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {keys.map((key: ApiKeyPublic) => (
                <tr key={key.id} className={key.revoked_at ? "opacity-40" : ""}>
                  <td>{key.name}</td>
                  <td><code className="text-xs">{key.prefix}</code></td>
                  <td>
                    <div className="flex gap-1 flex-wrap">
                      {key.scopes.map((s) => (
                        <code key={s} className="text-xs bg-[var(--color-code-bg)] px-1">
                          {s}
                        </code>
                      ))}
                    </div>
                  </td>
                  <td className="tabular-nums text-xs muted">
                    {key.last_used_at
                      ? new Date(key.last_used_at).toLocaleDateString()
                      : "Never"}
                  </td>
                  <td className="tabular-nums text-xs muted">
                    {new Date(key.created_at).toLocaleDateString()}
                  </td>
                  <td>
                    {key.revoked_at ? (
                      <span className="text-xs danger">Revoked</span>
                    ) : (
                      <span className="text-xs accent">Active</span>
                    )}
                  </td>
                  <td>
                    {!key.revoked_at && (
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => {
                          if (confirm(`Revoke key "${key.name}"? This cannot be undone.`)) {
                            revokeMutation.mutate(key.id);
                          }
                        }}
                      >
                        Revoke
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
              {keys.length === 0 && (
                <tr>
                  <td colSpan={7} className="text-center faint py-6">
                    No API keys yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </Layout>
  );
}
