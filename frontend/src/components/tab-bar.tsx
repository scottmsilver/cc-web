"use client";

export type TabId = "chat" | "files" | "artifacts" | "debug" | "terminal";

type TabConfig = {
  id: TabId;
  label: string;
  badge?: string;
};

function TabButton({
  tab,
  active,
  onClick,
}: {
  tab: TabConfig;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-3 text-sm font-medium ${
        active
          ? "border-b-2 border-[var(--th-accent)] text-[var(--th-accent)]"
          : "text-gray-700 hover:text-gray-900"
      }`}
    >
      {tab.label}{tab.badge ?? ""}
    </button>
  );
}

export function TabBar({
  activeTab,
  onTabChange,
  fileCount,
  hasProgress,
}: {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
  fileCount: number;
  hasProgress: boolean;
}) {
  const tabs: TabConfig[] = [
    { id: "chat", label: "Chat" },
    { id: "files", label: "Files", badge: fileCount > 0 ? ` (${fileCount})` : undefined },
    { id: "artifacts", label: "Artifacts" },
    { id: "debug", label: "Debug", badge: hasProgress ? " \u25CF" : undefined },
    { id: "terminal", label: "Terminal" },
  ];

  return (
    <>
      {tabs.map((tab) => (
        <TabButton
          key={tab.id}
          tab={tab}
          active={activeTab === tab.id}
          onClick={() => onTabChange(tab.id)}
        />
      ))}
    </>
  );
}
