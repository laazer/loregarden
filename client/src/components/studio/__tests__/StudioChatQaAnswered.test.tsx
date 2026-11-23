/**
 * A qa card the operator already answered in prose goes read-only.
 *
 * lg-baxter-chat-2742. `services/living_map/records.py:131` settles the Living
 * Map on `any(later.role == USER for later in messages[index+1:])` — a user
 * message after the card is the answer — so the Map read "Decided" while the
 * card read "Needs you" forever. Measured on the live database: 23 qa cards
 * across 11 threads, none carrying a stored answer, 22 of them followed by a
 * user reply.
 *
 * The fact comes from the thread, not from `part`: `QAPrimitive` cannot see the
 * message after its own, so a card asked to infer it would be inferring from
 * nothing. Both halves are tested here — the thread's backwards pass, through
 * `StudioChatMessages`, and the card's own rendering, through `QAPrimitive`
 * directly. A prop-level test alone would pass with the thread wired to nothing.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { useChatTurnThinking, type ChatTurnThinking } from "../../../hooks/useChatTurnThinking";
import { COMMENT } from "../../../test/contrast";
import type { ChatMessageView } from "../../chat/chatUtils";
import { QAPrimitive } from "../../chat/primitives/QAPrimitive";
import type { QAItem, QAPart } from "../../chat/primitives/types";
import { StudioChatMessages } from "../StudioChat";

jest.mock("../../../hooks/useChatTurnThinking", () => ({ useChatTurnThinking: jest.fn() }));

const IDLE_TURN: ChatTurnThinking = {
  content: "",
  answer: "",
  activity: "",
  plans: [],
  isStreaming: false,
};

beforeEach(() => {
  jest.mocked(useChatTurnThinking).mockReturnValue(IDLE_TURN);
});

const ITEMS: QAItem[] = [
  { id: "scope", question: "Who is this for?" },
  { id: "when", question: "By when?" },
];

/** The same part in every case, so the only variable is the thread around it. */
const QA: QAPart = { primitive: "qa", items: ITEMS };

function assistant(id: string, parts?: QAPart[]): ChatMessageView {
  return { id, role: "assistant", content: "", parts };
}

function user(id: string, content: string, displayContent?: string): ChatMessageView {
  return { id, role: "user", content, display_content: displayContent };
}

function thread(messages: ChatMessageView[]) {
  return render(<StudioChatMessages messages={messages} onPrimitiveSubmit={jest.fn()} />);
}

/** The one qa card in the rendered thread. */
function card(): HTMLElement {
  const found = document.querySelector("article.lg-primitive-card");
  if (!found) throw new Error("no primitive card rendered");
  return found as HTMLElement;
}

function chip(within: HTMLElement = card()): HTMLElement {
  const found = within.querySelector(".status-chip");
  if (!found) throw new Error("the card renders no status chip");
  return found as HTMLElement;
}

// ---------------------------------------------------------------------------
// The four cases the criteria name, each through the thread that supplies the
// fact.
// ---------------------------------------------------------------------------

describe("a qa card in a thread", () => {
  it("stays answerable as the last message in the thread", () => {
    thread([user("m0", "Here is what I want"), assistant("m1", [QA])]);

    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Send 2 answers" })).toBeInTheDocument();
    expect(chip()).toHaveTextContent("Needs you · 2");
    expect(card()).toHaveClass("lg-primitive-card--needs-you");
  });

  it("goes read-only and shows the reply once a user message follows it", () => {
    thread([assistant("m1", [QA]), user("m2", "Operators, by Friday.")]);

    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Send/ })).toBeNull();
    expect(card()).toHaveTextContent("Operators, by Friday.");
    expect(chip()).toHaveTextContent("Answered in the thread");
    expect(chip()).toHaveAttribute("data-tone", "done");
  });

  // 2 of the 23 live cards carry zero items. "Answered" over a card that never
  // asked anything is a new lie, not the fix.
  it("keeps the malformed-card message when it carried no questions at all", () => {
    thread([assistant("m1", [{ primitive: "qa", items: [] }]), user("m2", "Operators.")]);

    expect(card()).toHaveTextContent(/carried no questions/);
    expect(chip()).toHaveTextContent("Nothing to answer");
    expect(card().textContent).not.toMatch(/Answered/);
  });

  // The read-only flip must never discard text the operator typed and has not
  // sent — and the refetch that brings the reply down is exactly when it would.
  it("stays answerable when the reply arrives mid-typing", () => {
    const view = thread([assistant("m1", [QA])]);

    fireEvent.change(screen.getByLabelText("Who is this for?"), { target: { value: "Operators" } });
    view.rerender(
      <StudioChatMessages
        messages={[assistant("m1", [QA]), user("m2", "Operators, by Friday.")]}
        onPrimitiveSubmit={jest.fn()}
      />,
    );

    expect(screen.getByLabelText("Who is this for?")).toHaveValue("Operators");
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    expect(card().textContent).not.toMatch(/Answered in the thread/);
  });
});

// ---------------------------------------------------------------------------
// Which reply, and whether there is one at all.
// ---------------------------------------------------------------------------

describe("which later reply the thread hands down", () => {
  // Settled by spec (S1): existence is role-based, matching records.py's `any`;
  // display picks the FIRST user message after the card, because that is the
  // message that answered it. Taking the last would read plausibly and show the
  // wrong text on every thread that kept talking.
  it("picks the first user message after the card, not the last", () => {
    thread([
      assistant("m1", [QA]),
      assistant("m2"),
      user("m3", "first"),
      user("m4", "second"),
    ]);

    expect(card()).toHaveTextContent("first");
    expect(card().textContent).not.toMatch(/second/);
  });

  it("hands down nothing when only assistant messages follow", () => {
    thread([assistant("m1", [QA]), assistant("m2"), assistant("m3")]);

    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    expect(chip()).toHaveTextContent("Needs you · 2");
  });

  // A reply before the card is not an answer to it. A forward pass, or a plain
  // `messages.some(isUser)`, would pass every test above and fail this one.
  it("ignores a user message that came before the card", () => {
    thread([user("m0", "Here is what I want"), assistant("m1", [QA])]);

    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    expect(card().textContent).not.toMatch(/Here is what I want/);
  });

  // Spec R1 assumption 1 says a qa part on a USER message gets the same
  // treatment, applied by position alone. It cannot: `StudioChatMessages`
  // renders a user turn as a bubble and never calls `PrimitiveParts` for it, so
  // there is no card to flip and the "is a message its own later reply?"
  // off-by-one is unobservable from here. Pinned rather than dropped, so the
  // next reader does not go looking for the case the spec implies exists.
  it("renders no card at all for a qa part on a user message", () => {
    thread([{ id: "m1", role: "user", content: "and answer these", parts: [QA] }]);

    expect(document.querySelector("article.lg-primitive-card")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  // Each card is answered by the reply after *it*. One shared flag for the
  // thread would flip both.
  it("flips only the cards a reply actually follows", () => {
    thread([
      assistant("m1", [QA]),
      user("m2", "Operators, by Friday."),
      assistant("m3", [{ primitive: "qa", items: [{ id: "next", question: "Which workspace?" }] }]),
    ]);

    const cards = document.querySelectorAll("article.lg-primitive-card");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveTextContent("Answered in the thread");
    expect(cards[1]).toHaveTextContent("Needs you");
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
  });

  // `chatMessageBody` is `display_content ?? content`: the text the operator
  // sees in the thread is the text the card must show, or the card quotes
  // something the thread never said.
  it("shows the reply as the thread shows it, display_content first", () => {
    thread([assistant("m1", [QA]), user("m2", "raw/stored", "Operators, by Friday.")]);

    expect(card()).toHaveTextContent("Operators, by Friday.");
    expect(card().textContent).not.toMatch(/raw\/stored/);
  });

  // Settled by spec (S2), against the intuitive answer. An empty reply is not
  // an answer — but records.py settles the Map on role alone, so keeping the
  // card answerable puts it back in disagreement with the Map, which IS the
  // defect. Measured minimum live reply is 5 characters, so this is reachable
  // in principle rather than in practice.
  it("flips on a reply that carried no text, and says so", () => {
    thread([assistant("m1", [QA]), user("m2", "   ")]);

    expect(screen.queryByRole("textbox")).toBeNull();
    expect(chip()).toHaveTextContent("Answered in the thread");
    expect(card()).toHaveTextContent("The reply that followed carried no text.");
  });
});

// ---------------------------------------------------------------------------
// How a read-only card renders.
// ---------------------------------------------------------------------------

describe("a read-only qa card", () => {
  // Its one action while waiting is the textarea and Send; once read-only it
  // holds no control at all and the operator's next action is the thread
  // composer. No "Answer again", no "Edit", and nothing for tab to land on.
  it("contains no focusable control and adds no tab stop", () => {
    thread([assistant("m1", [QA]), user("m2", "Operators, by Friday.")]);

    expect(card().querySelectorAll("button, textarea, input, select, a, [tabindex]")).toHaveLength(0);
  });

  it("drops the needs-you edge and the needs-you tint", () => {
    thread([assistant("m1", [QA]), user("m2", "Operators, by Friday.")]);

    expect(card()).not.toHaveClass("lg-primitive-card--needs-you");
    const icon = card().querySelector(".lg-primitive-card-icon");
    expect(icon).toHaveClass("lg-primitive-card-icon--mute");
    expect(icon).not.toHaveClass("lg-primitive-card-icon--you");
  });

  it("says neither Needs you nor Not answered anywhere", () => {
    thread([assistant("m1", [QA]), user("m2", "Operators, by Friday.")]);

    expect(card().textContent).not.toMatch(/Needs you/);
    // The per-item placeholder is the other half of the same lie: a card that
    // was answered in prose did not leave question 2 unanswered.
    expect(card().textContent).not.toMatch(/Not answered/);
  });

  // "Answered in the thread" is distinct from "Answers sent", which is a submit
  // made in this session: the chip names where the answer came from.
  it("names where the answer came from, apart from a submit made here", () => {
    thread([assistant("m1", [QA]), user("m2", "Operators, by Friday.")]);

    expect(chip()).toHaveTextContent("Answered in the thread");
    expect(card().textContent).not.toMatch(/Answers sent/);
  });

  // One reply is not an answer to question 3 specifically, so it is one block
  // for the whole card. Per-item would read as three separate answers the
  // operator never gave.
  it("renders one answer block for the whole card, not one per item", () => {
    render(
      <QAPrimitive
        part={{
          primitive: "qa",
          items: [
            { id: "a", question: "Who?" },
            { id: "b", question: "When?" },
            { id: "c", question: "Where?" },
          ],
        }}
        onSubmit={jest.fn()}
        laterUserReply={{ text: "Operators, Friday, here." }}
      />,
    );

    expect(document.querySelectorAll(".lg-primitive-qa-reply")).toHaveLength(1);
    expect(document.querySelectorAll(".lg-primitive-qa-reply-text")).toHaveLength(1);
  });

  // 509 characters is the measured longest live reply. A clamp or a "show more"
  // is the same lie in a new place: the card would again be hiding what the
  // operator said.
  it("shows a 509-character reply in its entirety, wrapped", () => {
    const long = "A".repeat(254) + " " + "B".repeat(254);
    expect(long).toHaveLength(509);
    thread([assistant("m1", [QA]), user("m2", long)]);

    const text = card().querySelector(".lg-primitive-qa-reply-text");
    expect(text).not.toBeNull();
    expect(text?.textContent).toBe(long);
  });

  // The questions read first, then the reply: labelled text read in order after
  // the questions.
  it("puts the reply after the questions, under a visible label", () => {
    thread([assistant("m1", [QA]), user("m2", "Operators, by Friday.")]);

    const questions = card().querySelector(".lg-primitive-qa-list");
    const reply = card().querySelector(".lg-primitive-qa-reply");
    expect(questions).not.toBeNull();
    expect(reply).not.toBeNull();
    expect(
      Boolean((questions as Element).compareDocumentPosition(reply as Element) & Node.DOCUMENT_POSITION_FOLLOWING),
    ).toBe(true);
    expect(card().querySelector(".lg-primitive-qa-reply-label")?.textContent).toBeTruthy();
  });

  // Same input, same DOM: nothing about the flip may depend on render order or
  // a module-level carry.
  it("renders the same thing twice over", () => {
    const first = thread([assistant("m1", [QA]), user("m2", "Operators, by Friday.")]);
    const once = card().innerHTML;
    first.unmount();
    thread([assistant("m1", [QA]), user("m2", "Operators, by Friday.")]);
    expect(card().innerHTML).toBe(once);
  });
});

// ---------------------------------------------------------------------------
// The card's own contract: the prop, and the two exceptions.
// ---------------------------------------------------------------------------

describe("QAPrimitive's own prop", () => {
  // Five existing direct renders pass part and onSubmit only. The prop is
  // additive: absent or null is today's behaviour exactly.
  it.each([undefined, null])("stays answerable with laterUserReply %p", (later) => {
    render(<QAPrimitive part={QA} onSubmit={jest.fn()} laterUserReply={later} />);

    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Send 2 answers" })).toBeInTheDocument();
  });

  // The card must not reach into `part` for this. A part that happens to carry
  // the right-looking field proves nothing; what matters is that with no prop,
  // nothing flips.
  it("does not infer the reply from the part alone", () => {
    render(
      <QAPrimitive
        part={{ ...QA, prompt: "Operators, by Friday." } as QAPart}
        onSubmit={jest.fn()}
      />,
    );

    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    expect(document.querySelector(".lg-primitive-qa-reply")).toBeNull();
  });

  // Exception two. A draft is text that differs from what `initialAnswers`
  // seeded the state with — compared against `items[i].answer ?? ""`, not
  // against `""`, or a stored answer would read as a draft and the card would
  // never flip.
  it("keeps a card answerable while it holds an unsent draft", () => {
    const view = render(<QAPrimitive part={QA} onSubmit={jest.fn()} laterUserReply={null} />);

    fireEvent.change(screen.getByLabelText("Who is this for?"), { target: { value: "Operators" } });
    view.rerender(
      <QAPrimitive part={QA} onSubmit={jest.fn()} laterUserReply={{ text: "Operators, by Friday." }} />,
    );

    expect(screen.getByLabelText("Who is this for?")).toHaveValue("Operators");
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
    expect(document.body.textContent).not.toMatch(/Answered in the thread/);
  });

  // The other side of that comparison: items that arrive WITH stored answers
  // are not a draft, so a later reply still flips the card. A draft test written
  // as `answers.some((a) => a !== "")` passes the test above and fails this one.
  it("does not mistake a stored answer for a draft", () => {
    render(
      <QAPrimitive
        part={{
          primitive: "qa",
          items: [
            { id: "scope", question: "Who is this for?", answer: "Operators" },
            { id: "when", question: "By when?", answer: "Friday" },
          ],
        }}
        onSubmit={jest.fn()}
        laterUserReply={{ text: "Operators, by Friday." }}
      />,
    );

    expect(screen.queryByRole("textbox")).toBeNull();
    expect(document.querySelector(".status-chip")).toHaveTextContent("Answered in the thread");
  });

  // A draft typed and then cleared back to the seeded value is not a draft any
  // more, so the card flips. Without this, `everTyped`-style state would pass
  // the draft test and pin the card open forever.
  it("flips once a draft is cleared back to what it arrived with", () => {
    const view = render(<QAPrimitive part={QA} onSubmit={jest.fn()} laterUserReply={null} />);

    const field = screen.getByLabelText("Who is this for?");
    fireEvent.change(field, { target: { value: "Operators" } });
    fireEvent.change(field, { target: { value: "" } });
    view.rerender(
      <QAPrimitive part={QA} onSubmit={jest.fn()} laterUserReply={{ text: "Operators, by Friday." }} />,
    );

    expect(screen.queryByRole("textbox")).toBeNull();
    expect(document.querySelector(".status-chip")).toHaveTextContent("Answered in the thread");
  });

  // The local `submitted` flag wins: a card answered through its own Send in
  // this session keeps saying so, rather than being relabelled by the reply its
  // own submit produced.
  it("keeps the Answers sent chip for a submit made in this session", () => {
    const onSubmit = jest.fn();
    const view = render(<QAPrimitive part={QA} onSubmit={onSubmit} laterUserReply={null} />);

    fireEvent.change(screen.getByLabelText("Who is this for?"), { target: { value: "Operators" } });
    fireEvent.change(screen.getByLabelText("By when?"), { target: { value: "Friday" } });
    fireEvent.click(screen.getByRole("button", { name: "Send 2 answers" }));
    expect(onSubmit).toHaveBeenCalled();

    view.rerender(
      <QAPrimitive part={QA} onSubmit={onSubmit} laterUserReply={{ text: "Operators, by Friday." }} />,
    );

    expect(document.querySelector(".status-chip")).toHaveTextContent("Answers sent");
    expect(document.body.textContent).not.toMatch(/Answered in the thread/);
  });

  // Exception one, at the prop level: zero items keeps its own branch whatever
  // the prop holds.
  it("keeps the zero-item branch whatever the prop holds", () => {
    render(
      <QAPrimitive
        part={{ primitive: "qa", items: [] }}
        onSubmit={jest.fn()}
        laterUserReply={{ text: "Operators, by Friday." }}
      />,
    );

    expect(document.body.textContent).toMatch(/carried no questions/);
    expect(document.querySelector(".status-chip")).toHaveTextContent("Nothing to answer");
    expect(document.querySelector(".lg-primitive-qa-reply")).toBeNull();
  });

  // The card issues no request of its own: the flag arrives in the same thread
  // snapshot as the part, so there is no spinner and no error state to add.
  it("takes no loading or error state of its own", () => {
    const { container } = render(
      <QAPrimitive part={QA} onSubmit={jest.fn()} laterUserReply={{ text: "Operators, by Friday." }} />,
    );

    expect(container.querySelector("[aria-busy='true']")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The CSS the answer block depends on. jsdom loads no stylesheet, so the rules
// are read from the file: "no clamp" is a claim about the CSS, and asserting it
// on a computed style would assert nothing at all.
// ---------------------------------------------------------------------------

describe("the answer block's CSS", () => {
  const CSS = readFileSync(
    join(__dirname, "..", "..", "chat", "primitives", "PrimitiveCard.css"),
    "utf8",
  );
  // Comments first, for the reason `COMMENT` gives: a comment above a rule is
  // otherwise read as part of its selector and hides the rule from the scan.
  const rules = [...CSS.replace(COMMENT, "").matchAll(/([^{}@]+?)\s*\{([^{}]*)\}/g)]
    .map(([, selector, body]) => ({ selector: selector.trim(), body }))
    .filter((rule) => /\.lg-primitive-qa-reply/.test(rule.selector));

  it("declares the rules this card renders with", () => {
    const selectors = rules.map((r) => r.selector);
    expect(selectors).toContain(".lg-primitive-qa-reply");
    expect(selectors).toContain(".lg-primitive-qa-reply-label");
    expect(selectors).toContain(".lg-primitive-qa-reply-text");
  });

  it("wraps the reply rather than collapsing its newlines", () => {
    const text = rules.find((r) => r.selector === ".lg-primitive-qa-reply-text");
    expect(text?.body).toMatch(/white-space:\s*pre-wrap\s*;/);
  });

  // A clamped answer is the same lie in a new place, and the flip arrives on a
  // snapshot refetch rather than a gesture, so there is nothing for a
  // transition to describe.
  it("clamps nothing and animates nothing", () => {
    for (const rule of rules) {
      expect(rule.body).not.toMatch(/max-height/);
      expect(rule.body).not.toMatch(/line-clamp/);
      expect(rule.body).not.toMatch(/overflow:\s*hidden/);
      expect(rule.body).not.toMatch(/transition/);
      expect(rule.body).not.toMatch(/animation/);
    }
  });
});
