/**
 * Hierarchy Editor Data Models
 *
 * Implements composite pattern for hierarchy nodes (items and folders),
 * command pattern for reversible operations, and visitor pattern for traversal.
 */

// =============================================================================
// Composite Pattern: Hierarchy Nodes
// =============================================================================

export interface HierarchyNode {
  id: string;
  title: string;
  description: string;
  type: "item" | "folder";
  children: HierarchyNode[];
  parent?: HierarchyNode;
  addChild(child: HierarchyNode): void;
  removeChild(id: string): boolean;
  accept(visitor: HierarchyVisitor): void;
}

export class ProposalItem implements HierarchyNode {
  id: string;
  title: string;
  description: string;
  type = "item" as const;
  private _children: HierarchyNode[] = [];
  parent?: HierarchyNode;

  constructor(id: string, title: string, description: string = "") {
    this.id = id;
    this.title = title;
    this.description = description;
  }

  get children(): HierarchyNode[] {
    return this._children;
  }

  set children(value: HierarchyNode[]) {
    this._children = value;
  }

  getChildren(): HierarchyNode[] {
    return this._children;
  }

  addChild(_child: HierarchyNode): void {
    throw new Error("ProposalItem cannot have children");
  }

  removeChild(_id: string): boolean {
    throw new Error("ProposalItem cannot have children");
  }

  accept(visitor: HierarchyVisitor): void {
    visitor.visitProposalItem(this);
  }
}

export class ProposalFolder implements HierarchyNode {
  id: string;
  title: string;
  description: string;
  type = "folder" as const;
  private _children: HierarchyNode[] = [];
  parent?: HierarchyNode;

  constructor(id: string, title: string, description: string = "") {
    this.id = id;
    this.title = title;
    this.description = description;
  }

  get children(): HierarchyNode[] {
    return this._children;
  }

  set children(value: HierarchyNode[]) {
    this._children = value;
  }

  getChildren(): HierarchyNode[] {
    return this._children;
  }

  addChild(child: HierarchyNode): void {
    // Runtime guard against corrupted/mutated node state: the `type` field is
    // statically "folder" here, but callers can bypass the type system (e.g.
    // via `as any`) and mutate it at runtime, so re-check it defensively
    // instead of trusting static narrowing.
    if ((this.type as string) === "item") {
      throw new Error("ProposalItem cannot have children");
    }
    if (this._children.some((c) => c.id === child.id)) {
      throw new Error(`Child with id ${child.id} already exists`);
    }
    // Prevent multi-parent nodes: child cannot already have a different parent
    if (child.parent && child.parent !== this) {
      throw new Error(
        `Child ${child.id} already has parent ${child.parent.id}`
      );
    }
    child.parent = this;
    this._children.push(child);
  }

  removeChild(id: string): boolean {
    const index = this._children.findIndex((c) => c.id === id);
    if (index === -1) return false;
    const removed = this._children[index];
    removed.parent = undefined;
    this._children.splice(index, 1);
    return true;
  }

  insertChildAt(child: HierarchyNode, index: number): void {
    // Internal method for undo operations - inserts without duplicate checking
    // Assumes parent pointer is already set correctly
    this._children.splice(index, 0, child);
  }

  accept(visitor: HierarchyVisitor): void {
    visitor.visitProposalFolder(this);
    this._children.forEach((child) => child.accept(visitor));
  }
}

// =============================================================================
// Command Pattern for Reversible Operations
// =============================================================================

export interface Command {
  execute(): void;
  undo(): void;
}

export class EditTitleCommand implements Command {
  private node: HierarchyNode;
  private oldTitle: string;
  private newTitle: string;

  constructor(node: HierarchyNode, newTitle: string) {
    this.node = node;
    this.oldTitle = node.title;
    this.newTitle = newTitle;
  }

  execute(): void {
    this.node.title = this.newTitle;
  }

  undo(): void {
    this.node.title = this.oldTitle;
  }
}

export class EditDescriptionCommand implements Command {
  private node: HierarchyNode;
  private oldDescription: string;
  private newDescription: string;

  constructor(node: HierarchyNode, newDescription: string) {
    this.node = node;
    this.oldDescription = node.description;
    this.newDescription = newDescription;
  }

  execute(): void {
    this.node.description = this.newDescription;
  }

  undo(): void {
    this.node.description = this.oldDescription;
  }
}

export class AddChildCommand implements Command {
  private parent: HierarchyNode;
  private child: HierarchyNode;

  constructor(parent: HierarchyNode, child: HierarchyNode) {
    if (parent.type === "item") {
      throw new Error("Cannot add child to a ProposalItem");
    }
    this.parent = parent;
    this.child = child;
  }

  execute(): void {
    this.parent.addChild(this.child);
  }

  undo(): void {
    this.parent.removeChild(this.child.id);
  }
}

export class RemoveChildCommand implements Command {
  private parent: HierarchyNode;
  private child: HierarchyNode;
  private originalIndex: number;
  private savedParent: HierarchyNode | undefined;

  constructor(parent: HierarchyNode, child: HierarchyNode) {
    this.parent = parent;
    this.child = child;
    // Find the child in parent's children at construction time
    this.originalIndex = parent.children.findIndex((c) => c.id === child.id);
    // Save the parent state for robust undo (may be corrupted, so save it)
    this.savedParent = child.parent;
    // Validate that child either has this parent or is directly in the array
    // (Allow construction even if child is no longer in parent - may have been removed)
    if (this.originalIndex === -1 && this.savedParent !== parent) {
      throw new Error(`Child ${child.id} not found in parent`);
    }
  }

  execute(): void {
    // Only check children array, not parent pointer (parent pointer may be corrupted)
    const currentIndex = this.parent.children.findIndex((c) => c.id === this.child.id);
    if (currentIndex === -1) {
      // Child not in array (may have been removed by direct mutation)
      throw new Error("Child item not found in parent");
    }
    this.parent.removeChild(this.child.id);
  }

  undo(): void {
    // Restore the saved parent pointer and position in array
    this.child.parent = this.savedParent;
    if (this.savedParent && this.savedParent.type === "folder") {
      const folder = this.savedParent as unknown as ProposalFolder;
      folder.insertChildAt(this.child, this.originalIndex);
    }
  }
}

export class MoveChildCommand implements Command {
  private child: HierarchyNode;
  private oldParent: HierarchyNode;
  private newParent: HierarchyNode;
  private oldIndex: number;

  constructor(
    child: HierarchyNode,
    newParent: HierarchyNode,
    _newIndex?: number,
  ) {
    if (!child.parent) {
      throw new Error("Child has no parent");
    }
    if (newParent.type === "item") {
      throw new Error("Cannot move child to a ProposalItem");
    }
    // Check for circular reference
    if (this.isDescendantOf(newParent, child)) {
      throw new Error("Cannot move parent into its own child");
    }

    this.child = child;
    this.oldParent = child.parent;
    this.newParent = newParent;
    this.oldIndex = this.oldParent.children.findIndex((c) => c.id === child.id);
  }

  private isDescendantOf(ancestor: HierarchyNode, node: HierarchyNode): boolean {
    if (node.type === "item") return false;
    return node.children.some((child) => child.id === ancestor.id || this.isDescendantOf(ancestor, child));
  }

  execute(): void {
    // Validate state at execution time
    if (this.newParent.type === "item") {
      throw new Error("Cannot move child to a ProposalItem");
    }
    const currentIndex = this.oldParent.children.findIndex((c) => c.id === this.child.id);
    if (currentIndex === -1) {
      throw new Error(`Child ${this.child.id} not found in old parent`);
    }
    this.oldParent.removeChild(this.child.id);
    this.newParent.addChild(this.child);
  }

  undo(): void {
    this.newParent.removeChild(this.child.id);
    this.child.parent = this.oldParent;
    if (this.oldParent.type === "folder") {
      const folder = this.oldParent as unknown as ProposalFolder;
      folder.insertChildAt(this.child, this.oldIndex);
    }
  }
}

export class CommandHistory {
  private commands: Command[] = [];
  private pointer: number = -1;

  execute(command: Command): void {
    command.execute();
    this.commands.splice(this.pointer + 1);
    this.commands.push(command);
    this.pointer++;
  }

  undo(): boolean {
    if (this.pointer < 0) return false;
    this.commands[this.pointer].undo();
    this.pointer--;
    return true;
  }

  redo(): boolean {
    if (this.pointer >= this.commands.length - 1) return false;
    this.pointer++;
    this.commands[this.pointer].execute();
    return true;
  }

  canUndo(): boolean {
    return this.pointer >= 0;
  }

  canRedo(): boolean {
    return this.pointer < this.commands.length - 1;
  }

  clear(): void {
    this.commands = [];
    this.pointer = -1;
  }

  getPointer(): number {
    return this.pointer;
  }

  getCommandCount(): number {
    return this.commands.length;
  }
}

// =============================================================================
// Visitor Pattern for Validation
// =============================================================================

export interface ValidationError {
  nodeId: string;
  message: string;
}

export interface HierarchyVisitor {
  visitProposalItem(item: ProposalItem): void;
  visitProposalFolder(folder: ProposalFolder): void;
}

export class ValidityVisitor implements HierarchyVisitor {
  private errors: ValidationError[] = [];

  visitProposalItem(item: ProposalItem): void {
    if (!item.title || item.title.trim().length === 0) {
      this.errors.push({
        nodeId: item.id,
        message: "Title cannot be empty",
      });
    }
  }

  visitProposalFolder(folder: ProposalFolder): void {
    if (!folder.title || folder.title.trim().length === 0) {
      this.errors.push({
        nodeId: folder.id,
        message: "Folder name cannot be empty",
      });
    }
    if (folder.children.length === 0) {
      this.errors.push({
        nodeId: folder.id,
        message: "Folder must contain at least one child",
      });
    }
  }

  getErrors(): ValidationError[] {
    return this.errors;
  }

  isValid(): boolean {
    return this.errors.length === 0;
  }
}

// =============================================================================
// Observer Pattern for Real-time Validation Feedback
// =============================================================================

export interface ValidationObserver {
  onValidityChanged(isValid: boolean, errors: ValidationError[]): void;
}

export class HierarchyValidator {
  private observers: ValidationObserver[] = [];
  private isValid: boolean = true;
  private errors: ValidationError[] = [];

  subscribe(observer: ValidationObserver): void {
    this.observers.push(observer);
  }

  unsubscribe(observer: ValidationObserver): void {
    const index = this.observers.indexOf(observer);
    if (index > -1) {
      this.observers.splice(index, 1);
    }
  }

  validate(root: HierarchyNode): void {
    const visitor = new ValidityVisitor();
    root.accept(visitor);

    this.errors = visitor.getErrors();
    this.isValid = visitor.isValid();

    this.notifyObservers();
  }

  private notifyObservers(): void {
    this.observers.forEach((observer) => {
      observer.onValidityChanged(this.isValid, this.errors);
    });
  }

  getIsValid(): boolean {
    return this.isValid;
  }

  getErrors(): ValidationError[] {
    return this.errors;
  }
}

// =============================================================================
// Strategy Pattern for Validation Rules
// =============================================================================

export interface ValidationStrategy {
  validate(node: HierarchyNode): ValidationError[];
}

export class NonEmptyTitleStrategy implements ValidationStrategy {
  validate(node: HierarchyNode): ValidationError[] {
    if (!node.title || node.title.trim().length === 0) {
      return [{ nodeId: node.id, message: "Title cannot be empty" }];
    }
    return [];
  }
}

export class NoCircularReferenceStrategy implements ValidationStrategy {
  validate(node: HierarchyNode): ValidationError[] {
    if (node.type === "item") return [];

    const folder = node as unknown as ProposalFolder;
    return this.checkForCircularReferences(folder);
  }

  private checkForCircularReferences(
    node: HierarchyNode,
    ancestors: Set<string> = new Set(),
  ): ValidationError[] {
    if (ancestors.has(node.id)) {
      return [
        { nodeId: node.id, message: "Circular reference detected" },
      ];
    }

    const newAncestors = new Set(ancestors);
    newAncestors.add(node.id);

    const errors: ValidationError[] = [];
    for (const child of node.children) {
      errors.push(...this.checkForCircularReferences(child, newAncestors));
    }

    return errors;
  }
}
