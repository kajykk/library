'use client';

/** [[双链]] 联想浮层（从 NoteEditor 抽离） */

export interface MentionMenuState {
  query: string;
  top: number;
  left: number;
}

export interface MentionCandidate {
  id: string;
  title: string;
}

interface MentionMenuProps {
  state: MentionMenuState;
  candidates: MentionCandidate[];
  onSelect: (title: string) => void;
}

export default function MentionMenu({ state, candidates, onSelect }: MentionMenuProps) {
  if (candidates.length === 0) return null;
  return (
    <div
      className="absolute z-20 w-64 rounded-lg border border-gray-200 bg-white shadow-lg overflow-hidden"
      style={{ top: state.top + 24, left: state.left }}
    >
      {candidates.map((c) => (
        <button
          key={c.id}
          onClick={() => onSelect(c.title)}
          className="block w-full text-left px-3 py-2 text-sm text-gray-700 hover:bg-primary-50 truncate"
        >
          {c.title}
        </button>
      ))}
    </div>
  );
}
