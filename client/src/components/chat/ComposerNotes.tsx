import { useState } from "react";

import type { ComposerNote } from "../../api/composerApi";
import type { ComposerCommandsBinding } from "../../hooks/useComposerCommands";
import "./ComposerCommands.css";

/**
 * The post-it strip `/note` writes into, and the messages `/queue` is holding.
 *
 * Both sit above the composer because both are things the operator has already
 * typed and has not sent yet — a note by choice, a queued message because the
 * conversation is busy. Putting them in the transcript would claim they were
 * said; putting them in a drawer would hide the fact that they exist.
 */
export function ComposerNotes({ commands }: { commands: ComposerCommandsBinding }) {
  const { notes, draftNote, queued, helpCommands } = commands;
  if (!notes.length && draftNote === null && !queued.length && !helpCommands) return null;

  return (
    <div className="lg-composer-notes" role="group" aria-label="Notes and queued messages">
      {helpCommands ? (
        <div className="lg-composer-help" role="region" aria-label="Composer commands">
          <div className="lg-composer-help-header">
            <span className="lg-composer-queued-label">Commands</span>
            <button
              type="button"
              className="lg-composer-note-btn lg-composer-note-btn--quiet"
              aria-label="Dismiss help"
              onClick={commands.closeHelp}
            >
              ×
            </button>
          </div>
          <ul className="lg-composer-help-list">
            {helpCommands.map((command) => (
              <li key={command.name}>
                <code>/{command.name}</code>
                {command.aliases.length ? (
                  <span className="lg-composer-help-aliases">
                    {" "}
                    ({command.aliases.map((alias) => `/${alias}`).join(", ")})
                  </span>
                ) : null}
                <span className="lg-composer-help-summary"> — {command.summary}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {queued.map((entry) => (
        <div key={entry.id} className="lg-composer-queued" title="Sends as soon as the reply lands">
          <span className="lg-composer-queued-label">Queued</span>
          <span className="lg-composer-queued-body">{entry.content}</span>
          <button
            type="button"
            className="lg-composer-note-btn"
            onClick={() => commands.cancelQueued(entry.id)}
          >
            Cancel
          </button>
        </div>
      ))}
      {draftNote !== null ? (
        <NoteCard
          key="draft"
          initialBody={draftNote}
          startEditing
          onSave={(body) => commands.saveNote(body)}
          onDiscard={() => commands.setDraftNote(null)}
        />
      ) : null}
      {notes.map((note) => (
        <NoteCard
          key={note.id}
          note={note}
          initialBody={note.body}
          onSave={(body) => commands.updateNote(note.id, body)}
          onDiscard={() => commands.deleteNote(note.id)}
          onSend={() => commands.sendNote(note)}
          onSendInNewChat={
            commands.sendNoteInNewChat
              ? () => commands.sendNoteInNewChat?.(note)
              : undefined
          }
        />
      ))}
    </div>
  );
}

/**
 * One post-it.
 *
 * A saved note reads as text until it is clicked, so a strip of them stays
 * scannable; an unsaved one opens straight into its editor, because it was
 * created by someone who was mid-sentence.
 */
function NoteCard({
  note,
  initialBody,
  startEditing = false,
  onSave,
  onDiscard,
  onSend,
  onSendInNewChat,
}: {
  note?: ComposerNote;
  initialBody: string;
  startEditing?: boolean;
  onSave: (body: string) => void;
  onDiscard: () => void;
  onSend?: () => void;
  onSendInNewChat?: () => void;
}) {
  const [editing, setEditing] = useState(startEditing);
  const [body, setBody] = useState(initialBody);
  const trimmed = body.trim();

  return (
    <div className={`lg-composer-note${note?.sent_at ? " is-sent" : ""}`}>
      {editing ? (
        <textarea
          className="lg-composer-note-input"
          value={body}
          rows={3}
          autoFocus
          placeholder="Write it down now, send it when you want…"
          aria-label="Note"
          onChange={(event) => setBody(event.target.value)}
        />
      ) : (
        <button
          type="button"
          className="lg-composer-note-body"
          title="Edit this note"
          onClick={() => setEditing(true)}
        >
          {body}
        </button>
      )}

      <div className="lg-composer-note-actions">
        {editing ? (
          <>
            <button
              type="button"
              className="lg-composer-note-btn lg-composer-note-btn--primary"
              disabled={!trimmed}
              onClick={() => {
                onSave(trimmed);
                setEditing(false);
              }}
            >
              Keep
            </button>
            <button type="button" className="lg-composer-note-btn" onClick={onDiscard}>
              Discard
            </button>
          </>
        ) : (
          <>
            {onSend ? (
              <button
                type="button"
                className="lg-composer-note-btn lg-composer-note-btn--primary"
                disabled={!trimmed}
                onClick={onSend}
              >
                Send
              </button>
            ) : null}
            {onSendInNewChat ? (
              <button
                type="button"
                className="lg-composer-note-btn"
                disabled={!trimmed}
                onClick={onSendInNewChat}
              >
                Send in new chat
              </button>
            ) : null}
            <button
              type="button"
              className="lg-composer-note-btn lg-composer-note-btn--quiet"
              aria-label="Delete note"
              title="Delete note"
              onClick={onDiscard}
            >
              ×
            </button>
          </>
        )}
      </div>
    </div>
  );
}
