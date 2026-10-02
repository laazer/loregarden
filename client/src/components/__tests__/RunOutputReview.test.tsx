/**
 * The run-output line review. Pinned: that commenting is reachable and
 * operable from the keyboard, that a comment goes to the line it was written
 * on, that the approve control only exists when something can approve, and that
 * an empty stream is not drawn as a blank row.
 */

import { fireEvent, render, screen } from '@testing-library/react';

import { RunOutputReview, type OutputLine } from '../RunOutputReview';

function lines(...contents: string[]): OutputLine[] {
  return contents.map((content, i) => ({ number: i + 1, content, comments: [] }));
}

describe('RunOutputReview', () => {
  it('opens a composer on the chosen line and sends the comment there', () => {
    const onAddComment = jest.fn();
    render(<RunOutputReview outputType="stdout" lines={lines('a', 'b')} onAddComment={onAddComment} />);

    fireEvent.click(screen.getByRole('button', { name: 'Comment on line 2' }));
    const box = screen.getByRole('textbox', { name: 'Comment on line 2' });
    fireEvent.change(box, { target: { value: 'look here' } });
    fireEvent.click(screen.getByRole('button', { name: 'Comment' }));

    expect(onAddComment).toHaveBeenCalledWith(2, 'look here');
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('sends with Cmd/Ctrl+Enter and closes on Escape without sending', () => {
    const onAddComment = jest.fn();
    render(<RunOutputReview outputType="stdout" lines={lines('a')} onAddComment={onAddComment} />);

    fireEvent.click(screen.getByRole('button', { name: 'Comment on line 1' }));
    let box = screen.getByRole('textbox');
    fireEvent.change(box, { target: { value: 'draft' } });
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(onAddComment).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Comment on line 1' }));
    box = screen.getByRole('textbox');
    fireEvent.change(box, { target: { value: 'sent' } });
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true });
    expect(onAddComment).toHaveBeenCalledWith(1, 'sent');
  });

  it('does not send an empty comment', () => {
    const onAddComment = jest.fn();
    render(<RunOutputReview outputType="stdout" lines={lines('a')} onAddComment={onAddComment} />);

    fireEvent.click(screen.getByRole('button', { name: 'Comment on line 1' }));
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter', metaKey: true });

    expect(onAddComment).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Comment' })).toBeDisabled();
  });

  it('draws no approve control when nothing can approve, and one when it can', () => {
    const { rerender } = render(<RunOutputReview outputType="stdout" lines={lines('a')} />);
    expect(screen.queryByRole('button', { name: /approve/i })).not.toBeInTheDocument();

    const onApprove = jest.fn();
    rerender(<RunOutputReview outputType="stdout" lines={lines('a')} onApprove={onApprove} />);
    fireEvent.click(screen.getByRole('button', { name: /approve/i }));
    expect(onApprove).toHaveBeenCalled();
  });

  it('toggles wrapping and reports the state to assistive tech', () => {
    render(<RunOutputReview outputType="stdout" lines={lines('a')} />);
    const toggle = screen.getByRole('button', { name: 'Wrap lines' });

    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
  });

  it('renders an empty stream as an empty state, with no rows and no controls', () => {
    const { container } = render(
      <RunOutputReview outputType="stderr" lines={[]} onAddComment={jest.fn()} />,
    );

    expect(container.querySelectorAll('.run-output-row')).toHaveLength(0);
    expect(container.querySelector('.run-output-empty')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Wrap lines' })).not.toBeInTheDocument();
  });

  it("shows a line's comments beneath it and marks the line", () => {
    const withComment: OutputLine[] = [
      {
        number: 1,
        content: 'a',
        comments: [{ line_number: 1, content: 'why?', created_at: '2026-10-02T10:00:00Z' }],
      },
    ];
    const { container } = render(<RunOutputReview outputType="stdout" lines={withComment} />);

    expect(screen.getByText('why?')).toBeInTheDocument();
    expect(container.querySelector('.run-output-row')).toHaveClass('has-comments');
  });
});
