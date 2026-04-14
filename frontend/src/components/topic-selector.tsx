"use client";

import { useRef, useEffect, useState } from "react";
import type { Topic } from "@/lib/types";

type TopicSelectorProps = {
  topics: Topic[];
  activeTopic: string | null;
  activeSession: string | null;
  onSelectTopic: (slug: string, sessionId: string) => void;
  onNewTopic: () => void;
  onDeleteTopic: (slug: string) => void;
};

export function TopicSelector({
  topics,
  activeTopic,
  activeSession,
  onSelectTopic,
  onNewTopic,
  onDeleteTopic,
}: TopicSelectorProps) {
  const [open, setOpen] = useState(false);
  const [expandedTopic, setExpandedTopic] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
        setExpandedTopic(null);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const activeTopicObj = topics.find((t) => t.slug === activeTopic);

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="true"
        aria-expanded={open}
        className="flex items-center gap-1.5 rounded-lg border border-th-border px-3 py-1.5 text-xs text-th-text transition-colors hover:border-th-accent hover:text-th-text"
      >
        <span className="max-w-[200px] truncate transition-opacity duration-500">
          {activeTopicObj ? activeTopicObj.name : "New topic"}
        </span>
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open && (
        <div className="absolute right-0 top-full z-40 mt-1 w-80 rounded-lg border border-th-border bg-th-bg shadow-xl">
          <button
            onClick={() => {
              onNewTopic();
              setOpen(false);
              setExpandedTopic(null);
            }}
            className="w-full border-b border-th-border px-4 py-2.5 text-left text-sm text-th-accent hover:bg-th-surface"
          >
            + New topic
          </button>
          <div className="max-h-80 overflow-y-auto">
            {topics.length === 0 ? (
              <p className="px-4 py-3 text-xs text-th-text-muted">
                No topics yet
              </p>
            ) : (
              topics.map((topic) => (
                <div key={topic.slug}>
                  {/* Topic row */}
                  <div
                    className={`flex items-center px-4 py-2 text-sm hover:bg-th-surface ${
                      activeTopic === topic.slug
                        ? "bg-th-surface-hover text-th-text"
                        : "text-th-text-muted"
                    }`}
                  >
                    <button
                      onClick={() => {
                        if (expandedTopic === topic.slug) {
                          setExpandedTopic(null);
                        } else {
                          setExpandedTopic(topic.slug);
                          // Select the most recent conversation (last in array)
                          if (topic.conversations.length > 0) {
                            const latest = topic.conversations[topic.conversations.length - 1];
                            onSelectTopic(topic.slug, latest.session_id);
                          } else {
                            onSelectTopic(topic.slug, "");
                          }
                          setOpen(false);
                        }
                      }}
                      className="flex-1 min-w-0 text-left"
                    >
                      <div className="truncate text-sm transition-opacity duration-300">
                        {topic.name}
                      </div>
                      <div className="truncate text-xs text-th-text-faint">
                        {topic.conversations.length}{" "}
                        {topic.conversations.length === 1
                          ? "conversation"
                          : "conversations"}
                      </div>
                    </button>
                    {/* Expand/collapse chevron */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setExpandedTopic(
                          expandedTopic === topic.slug ? null : topic.slug,
                        );
                      }}
                      className="ml-1 rounded p-1 text-th-text-muted hover:bg-th-surface-hover hover:text-th-text"
                      title="Show conversations"
                    >
                      <svg
                        width="12"
                        height="12"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        className={`transition-transform ${
                          expandedTopic === topic.slug ? "rotate-180" : ""
                        }`}
                      >
                        <polyline points="6 9 12 15 18 9" />
                      </svg>
                    </button>
                    {/* Delete button */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        void onDeleteTopic(topic.slug);
                      }}
                      className="ml-1 rounded p-1 text-th-text-muted hover:bg-th-surface-hover hover:text-th-accent"
                      title="Delete topic"
                    >
                      ✕
                    </button>
                  </div>
                  {/* Expanded conversations */}
                  {expandedTopic === topic.slug &&
                    topic.conversations.length > 0 && (
                      <div className="border-l-2 border-th-border ml-6">
                        {topic.conversations.map((conv) => (
                          <button
                            key={conv.id}
                            onClick={() => {
                              onSelectTopic(topic.slug, conv.session_id);
                              setOpen(false);
                              setExpandedTopic(null);
                            }}
                            className={`flex w-full items-center px-3 py-1.5 text-left text-xs hover:bg-th-surface ${
                              activeSession === conv.session_id
                                ? "bg-th-surface-hover text-th-text"
                                : "text-th-text-muted"
                            }`}
                          >
                            <div className="min-w-0 flex-1">
                              <div className="truncate">
                                {conv.title || conv.id}
                              </div>
                              <div className="truncate text-th-text-faint">
                                {conv.status}
                              </div>
                            </div>
                          </button>
                        ))}
                      </div>
                    )}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
