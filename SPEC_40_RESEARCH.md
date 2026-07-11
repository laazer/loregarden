# Research Summary: Editable Hierarchy Editor for AI-Generated Proposals

**Ticket:** 40-build-editable-hierarchy-editor-for-proposal  
**Stage:** Specification  
**Agent:** Research Librarian (Blobert)  
**Date:** 2026-07-11

## Executive Summary

This research provides pre-loaded citations and architectural patterns for building an editable hierarchy editor UI. The implementation should leverage:
- **Composite pattern** for hierarchy representation
- **Command pattern** for reversible operations and undo/redo
- **Observer pattern** for reactive validation feedback
- **Visitor pattern** for tree traversal and validation

These four patterns form a cohesive architecture meeting all acceptance criteria.

---

## Sources Consulted

| Source | Tier | Why Selected |
|--------|------|-------------|
| Game Programming Patterns | 2 | Authoritative undo/redo and Command pattern architecture |
| RefactoringGuru Design Patterns | 2 | Core patterns: Composite, Observer, Memento, Visitor, Strategy |
| Red Blob Games | 2 | Interactive visualization and UI interaction design patterns |
| Martin Fowler's EAA Patterns | 2 | Presentation layer patterns for editing interfaces |

---

## Findings

### 1. Composite Pattern for Hierarchy Representation
**Confidence: High** — [RefactoringGuru](https://refactoring.guru/design-patterns/composite)

The Composite pattern is the foundational architecture for representing hierarchical tree structures. It enables treating individual nodes and container nodes uniformly through a shared interface.

**Key Principles:**
- **Component interface**: Defines common operations all elements support
- **Leaf nodes**: Individual proposal items that return direct results
- **Container nodes**: Folders that delegate to children and aggregate results
- **Recursive processing**: Traverse entire trees without type checking

**Implementation Pattern:**
```
interface HierarchyNode:
  - getTitle(): string
  - setTitle(string)
  - getDescription(): string
  - setDescription(string)
  - getChildren(): HierarchyNode[]
  - addChild(HierarchyNode)
  - removeChild(HierarchyNode)
  - accept(visitor)

class ProposalItem implements HierarchyNode:
  # No children (leaf node)

class ProposalFolder implements HierarchyNode:
  # Can have children (container node)
```

**Application to Ticket 40:**
- Each proposal item (leaf) and folder (container) implements the same interface
- Supports "add/remove hierarchy levels" through uniform interface
- "Edit titles and descriptions" applies to both types uniformly
- Enables "reorganize structure" through addChild/removeChild on any node

---

### 2. Command Pattern for Undo/Redo Architecture
**Confidence: High** — [Game Programming Patterns](https://gameprogrammingpatterns.com/command.html)

The Command pattern encapsulates each editing action as an object with `execute()` and `undo()` methods, enabling reversible operations.

**Core Architecture:**
```cpp
class Command {
  virtual void execute() = 0;
  virtual void undo() = 0;
};

class EditTitleCommand : public Command {
  Node* node_;
  string oldTitle_, newTitle_;
  
  void execute() override {
    oldTitle_ = node_->title();
    node_->setTitle(newTitle_);
  }
  
  void undo() override {
    node_->setTitle(oldTitle_);
  }
};
```

**History Management:**
- Maintain a list of executed commands with a current pointer
- **Execute**: Append to list, advance pointer
- **Undo**: Call `undo()` on current command, move pointer backward
- **Redo**: Advance pointer, re-execute command
- **New command after undo**: Discard all forward history (prevents branching)

**Memory Efficiency:**
Rather than snapshotting entire hierarchy state at each step, only capture the specific changes each command makes. This scales well for long undo histories.

**Application to Ticket 40:**
- "Edit titles and descriptions" → `EditTitleCommand`, `EditDescriptionCommand`
- "Add/remove hierarchy levels" → `AddChildCommand`, `RemoveChildCommand`
- "Reorganize structure" → `MoveChildCommand`, `ReorderChildrenCommand`
- "Undo/discard changes" → Execute/undo command history with pointer management

---

### 3. Observer Pattern for Validation Feedback
**Confidence: High** — [RefactoringGuru](https://refactoring.guru/design-patterns/observer)

The Observer pattern automatically notifies UI components when hierarchy state changes, enabling "visual feedback on hierarchy validity" without tight coupling.

**Core Mechanism:**
- Publisher (hierarchy model) maintains array of subscriber references
- When state changes, publisher calls `update()` on all subscribers
- Subscribers implement standardized interface and react independently
- Changes propagate automatically; no polling required

**Implementation Pattern:**
```
interface HierarchyObserver:
  onTitleChanged(node, newTitle)
  onChildAdded(parent, child)
  onChildRemoved(parent, child)
  onValidityChanged(isValid)

class ValidityIndicatorUI implements HierarchyObserver:
  onValidityChanged(isValid):
    if isValid:
      showGreen()
    else:
      showRed()
      showValidationErrors()
```

**Application to Ticket 40:**
- Validation system publishes `onValidityChanged(isValid)` when hierarchy state changes
- UI components subscribe to receive events
- "Visual feedback on hierarchy validity" displays automatically without polling
- Decouples validation logic from UI rendering

---

### 4. Visitor Pattern for Validation Traversal
**Confidence: High** — [RefactoringGuru](https://refactoring.guru/design-patterns/visitor)

The Visitor pattern enables operations across tree nodes without modifying element classes, perfect for validation and analysis.

**Core Mechanism:**
```
interface HierarchyVisitor:
  visitProposalItem(item)
  visitProposalFolder(folder)

class ValidityVisitor implements HierarchyVisitor:
  errors = []
  
  visitProposalItem(item):
    if item.title.isEmpty():
      errors.add("Title cannot be empty")
    if item.description.isEmpty():
      errors.add("Description cannot be empty")
  
  visitProposalFolder(folder):
    # Check folder has valid name
    # Recursively visit children
```

**Double Dispatch:**
- Client calls `node.accept(visitor)`
- Node calls `visitor.visitNodeType(this)` with correct type
- Correct visitor method executes without casting or type checking

**Application to Ticket 40:**
- "Visual feedback on hierarchy validity" → ValidityVisitor traverses tree
- Can implement SerializationVisitor for exporting hierarchy
- Can implement DiffVisitor for tracking what changed
- New operations can be added without modifying hierarchy classes

---

### 5. Memento Pattern for State Snapshots (Alternative to Command)
**Confidence: High** — [RefactoringGuru](https://refactoring.guru/design-patterns/memento)

The Memento pattern provides an alternative undo mechanism by capturing and restoring full state, useful when reverse operations are complex.

**Architecture:**
```
class HierarchyMemento:
  # Stores snapshot of entire hierarchy state

class HierarchyCaretaker:
  # Stores list of Mementos
  # Manages undo/redo pointer

Originator (Hierarchy):
  # Creates mementos
  # Restores from mementos
```

**Trade-off with Command Pattern:**
- **Command**: Efficient for large hierarchies, requires implementing undo logic
- **Memento**: Simple to implement, can consume more memory with snapshots

**Application:** Can combine both patterns—use Command for typical edits, Memento for complex structural changes.

---

### 6. Strategy Pattern for Validation Rules
**Confidence: Medium** — [RefactoringGuru](https://refactoring.guru/design-patterns/strategy)

The Strategy pattern encapsulates different validation rules, enabling pluggable validation.

**Implementation Pattern:**
```
interface ValidationStrategy:
  validate(node) → ValidationResult

class NonEmptyTitleStrategy implements ValidationStrategy:
  validate(node):
    if node.title.isEmpty():
      return INVALID("Title required")
    return VALID()

class CircularReferenceStrategy implements ValidationStrategy:
  validate(node):
    # Check if moving node would create circular reference
```

**Application to Ticket 40:**
- "Visual feedback on hierarchy validity" → Composite of validation strategies
- Each validation rule is independent and testable
- New validation rules can be added without modifying existing code

---

### 7. Interactive UI Patterns: Drag, Edit, Real-time Feedback
**Confidence: High** — [Red Blob Games](https://www.redblobgames.com/making-of/line-drawing/)

Production interactive diagrams use these patterns for drag-to-reorder and real-time feedback:

**Drag Operations:**
- Event handlers detect mouse/touch drag
- Validate constraints during drag (e.g., can't drop into self)
- Visual feedback: cursor changes, elements highlight
- Real-time updates as drag progresses

**Reactive Updates:**
- Update function registry triggers all dependent UI when data changes
- Single source of truth (hierarchy model)
- Cascading updates maintain consistency
- No manual re-rendering required

**Layer-Based Architecture:**
- Grid/background layer (visual reference)
- Interactive elements layer (draggable nodes)
- Labels/annotations layer (text)
- Feedback layer (validation indicators)

**Application to Ticket 40:**
- Drag-to-reorder: Detect drag, validate target, update model, cascade UI updates
- Inline editing: Click to edit, escape to discard, enter to confirm
- Real-time validation: Observer pattern propagates validity changes
- Visual feedback: Color, icons, tooltips indicate validity state

---

## Architectural Integration

### Recommended Component Structure

```
HierarchyEditor (UI Component)
├── HierarchyModel (Composite-based data structure)
│   ├── ProposalItem (leaf)
│   └── ProposalFolder (container)
├── CommandHistory (Command pattern undo/redo)
│   └── Command objects (EditTitleCommand, AddChildCommand, etc.)
├── HierarchyValidator (Visitor + Strategy patterns)
│   ├── Visitors (ValidityVisitor, DiffVisitor, etc.)
│   └── Validation Strategies (NonEmptyTitle, NoCircularRef, etc.)
├── UIObservers (Observer pattern)
│   ├── ValidityIndicatorUI
│   ├── TreeViewUI
│   └── EditPanelUI
└── InteractionHandlers (Interactive patterns)
    ├── DragReorderHandler
    ├── InlineEditHandler
    └── ContextMenuHandler
```

### State Flow

1. **User edits** → InteractionHandler converts to Command
2. **Command.execute()** → HierarchyModel changes
3. **Model notifies** → Observers receive events (Observer pattern)
4. **Validation runs** → ValidityVisitor traverses tree (Visitor pattern)
5. **UI updates** → Observer-subscribed components render
6. **Command stored** → CommandHistory maintains undo capability

---

## Gaps & Limitations

### 1. Godot-Specific Implementation
**Gap:** No Godot 4.x documentation on TreeItem/Tree controls fetched.

**Recommendation:** 
- Research Godot Engine source code (github.com/godotengine/godot) for SceneTreeEditor patterns
- Consult Godot 4.x docs (docs.godotengine.org) for Control, Tree, ItemList, or custom UI implementation
- Check Godot demo projects for tree-based UI examples

### 2. Performance at Scale
**Gap:** Patterns focus on typical hierarchies. Large hierarchies (1000+ nodes) require optimization.

**Recommendation:**
- Virtual/lazy scrolling for rendering only visible nodes
- Incremental validation (not validating entire tree on each change)
- Command history size limits or compression strategies
- Diff-based updates instead of full traversal on each change

### 3. Circular Reference Prevention
**Gap:** Strategy pattern covers validation, but detection algorithms not detailed.

**Recommendation:**
- Implement cycle detection during MoveChildCommand execution
- Prevent drop target selection if move would create cycle
- Validate before executing command, not after

### 4. Collaborative/Concurrent Editing
**Gap:** Command pattern assumes single-user undo history.

**Recommendation:** If multi-user required, research:
- Operational Transformation (OT)
- Conflict-free Replicated Data Types (CRDT)
- These are separate from single-user patterns in this research

---

## Recommended Next Steps for Implementation Agent

1. **Architecture Design Phase:**
   - Define HierarchyNode composite interface
   - Design Command class hierarchy
   - Plan Observer notification system
   - Design validation strategy system

2. **Data Structure Implementation:**
   - Implement Composite (ProposalItem, ProposalFolder)
   - Implement Command base + concrete commands
   - Implement CommandHistory with undo/redo pointer

3. **UI Implementation:**
   - Build tree view rendering (Visitor pattern)
   - Implement drag-to-reorder handlers
   - Implement inline editing
   - Wire Observer notifications to UI updates

4. **Validation System:**
   - Implement validation strategies
   - Implement ValidityVisitor
   - Wire validation to UI feedback

5. **Testing:**
   - Unit tests for each Command type
   - Undo/redo history tests (forward, backward, branch behavior)
   - Validation tests with various hierarchy structures
   - UI interaction tests (drag, edit, feedback)

---

## References

- **Game Programming Patterns - Command:** https://gameprogrammingpatterns.com/command.html
- **RefactoringGuru - Composite:** https://refactoring.guru/design-patterns/composite
- **RefactoringGuru - Command:** https://refactoring.guru/design-patterns/command
- **RefactoringGuru - Observer:** https://refactoring.guru/design-patterns/observer
- **RefactoringGuru - Visitor:** https://refactoring.guru/design-patterns/visitor
- **RefactoringGuru - Memento:** https://refactoring.guru/design-patterns/memento
- **RefactoringGuru - Strategy:** https://refactoring.guru/design-patterns/strategy
- **Red Blob Games - Interactive Diagrams:** https://www.redblobgames.com/making-of/line-drawing/
- **Martin Fowler - EAA Patterns:** https://martinfowler.com/eaaDev/
