#!/usr/bin/env python3
"""Patch qwen-code-webui server bundle so conversation history lists correctly.

Two bugs in the history-list endpoint
``GET /api/projects/:encodedProjectName/histories`` cause the WebUI "view
conversation history" view to show nothing, so opening a project starts a
brand-new chat even though history exists on disk:

1. ``getHistoryFiles(historyDir)`` only scans ``*.jsonl`` directly under
   ``~/.qwen/projects/<encodedProjectName>``, but qwen-code-cli stores
   sessions in ``<historyDir>/chats/<sessionId>.jsonl``. The list is always
   empty. (The per-session endpoint reads ``chats/`` explicitly and works.)
   Fix: after scanning the root dir, also scan the ``chats/`` subdirectory.

2. ``groupConversations(conversationFiles)`` dedupes conversations whose
   ``messageIds`` are a subset of another conversation's. qwen-code-cli
   writes assistant messages without ``message.id`` (it is ``null``), so
   every conversation's ``messageIds`` set is empty and ``isSubset(empty,
   anything)`` is always true — all files after the first are dropped as
   "duplicates". Fix: only attempt the subset-dedup when the current
   conversation actually has message ids; a conversation with no ids is
   always kept, so each ``.jsonl`` file shows up as its own session.

3. ``parseHistoryFile`` builds the conversation preview only from
   ``message.role === "assistant"`` + ``message.content``, but qwen-code-cli
   writes ``message.role === "model"`` with the text in ``message.parts``
   (first part may be a ``thought``). Every list item therefore showed
   "No preview available". Fix: also accept role ``"model"``, fall back to
   ``message.parts``, and skip thought parts when picking the preview text.

This patches the server bundle at
``/usr/lib/node_modules/qwen-code-webui/dist/cli/node.js``.
It is pinned to qwen-code-webui@0.2.40 (see Dockerfile); if the upstream
bundle changes, this script exits non-zero so the build fails loudly instead
of silently shipping an unpatched bundle.
"""

import sys

BUNDLE = "/usr/lib/node_modules/qwen-code-webui/dist/cli/node.js"

# Exact text from qwen-code-webui@0.2.40 (unminified server bundle).
OLD_FN = """function getHistoryFiles(historyDir) {
  try {
    const files = [];
    for await (const entry of readDir(historyDir)) {
      if (entry.isFile && entry.name.endsWith(\".jsonl\")) {
        files.push(`${historyDir}/${entry.name}`);
      }
    }
    return files;
  } catch {
    return [];
  }
}"""

NEW_FN = """function getHistoryFiles(historyDir) {
  try {
    const files = [];
    for await (const entry of readDir(historyDir)) {
      if (entry.isFile && entry.name.endsWith(\".jsonl\")) {
        files.push(`${historyDir}/${entry.name}`);
      }
    }
    const chatsDir = `${historyDir}/chats`;
    try {
      for await (const entry of readDir(chatsDir)) {
        if (entry.isFile && entry.name.endsWith(\".jsonl\")) {
          files.push(`${chatsDir}/${entry.name}`);
        }
      }
    } catch {}
    return files;
  } catch {
    return [];
  }
}"""

# Bug 2: dedup logic collapses every CLI conversation (assistant messages have
# no message.id -> empty messageIds -> empty set is a subset of everything).
OLD_GROUP = """  for (const currentConv of sortedConversations) {
    const isSubsetOfExisting = uniqueConversations.some(
      (existingConv) => isSubset(currentConv.messageIds, existingConv.messageIds)
    );
    if (!isSubsetOfExisting) {"""

NEW_GROUP = """  for (const currentConv of sortedConversations) {
    const isSubsetOfExisting = currentConv.messageIds.size > 0 && uniqueConversations.some(
      (existingConv) => isSubset(currentConv.messageIds, existingConv.messageIds)
    );
    if (!isSubsetOfExisting) {"""

# Bug 3: conversation preview extraction only matched role "assistant" with
# message.content, but qwen-code-cli writes role "model" with message.parts
# (first part may be a "thought"). Preview therefore always fell back to
# "No preview available".
OLD_PREVIEW = """        if (parsed.message?.role === \"assistant\" && parsed.message?.content) {
          const content2 = parsed.message.content;
          if (Array.isArray(content2)) {
            for (const item of content2) {
              if (typeof item === \"object\" && item && \"text\" in item) {
                lastMessagePreview = String(item.text).substring(0, 100);
                break;
              }
            }
          }
        }"""

NEW_PREVIEW = """        if ((parsed.message?.role === \"assistant\" || parsed.message?.role === \"model\") && (parsed.message?.content || parsed.message?.parts)) {
          const content2 = parsed.message.content || parsed.message.parts;
          if (Array.isArray(content2)) {
            for (const item of content2) {
              if (typeof item === \"object\" && item && \"text\" in item && !item.thought) {
                lastMessagePreview = String(item.text).substring(0, 100);
                break;
              }
            }
          }
        }"""


def main() -> int:
    try:
        with open(BUNDLE, encoding="utf-8") as f:
            data = f.read()
    except OSError as exc:
        print(f"[patch-qwen-webui-histories] cannot read bundle: {exc}", file=sys.stderr)
        return 1

    for label, old, new in (
        ("getHistoryFiles", OLD_FN, NEW_FN),
        ("groupConversations", OLD_GROUP, NEW_GROUP),
        ("lastMessagePreview", OLD_PREVIEW, NEW_PREVIEW),
    ):
        if old not in data:
            # Already patched (new present) counts as success; drift fails.
            if new in data:
                print(
                    f"[patch-qwen-webui-histories] {label} already patched, skipping",
                    file=sys.stderr,
                )
                continue
            print(
                f"[patch-qwen-webui-histories] {label} OLD pattern not found — version drift?",
                file=sys.stderr,
            )
            return 1

        if data.count(old) != 1:
            print(
                f"[patch-qwen-webui-histories] {label} pattern not unique — "
                "aborting to avoid corrupting the bundle",
                file=sys.stderr,
            )
            return 1

        data = data.replace(old, new)

    try:
        with open(BUNDLE, "w", encoding="utf-8") as f:
            f.write(data)
    except OSError as exc:
        print(f"[patch-qwen-webui-histories] cannot write bundle: {exc}", file=sys.stderr)
        return 1

    print(
        "[patch-qwen-webui-histories] patched getHistoryFiles (chats/ scan), "
        "groupConversations (keep id-less sessions) and "
        "lastMessagePreview (CLI model/parts format) OK"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
