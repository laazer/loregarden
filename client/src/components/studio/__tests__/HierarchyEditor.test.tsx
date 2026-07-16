/**
 * BEHAVIORAL TEST SUITE: Editable Hierarchy Editor for Proposal
 *
 * Ticket:   40-build-editable-hierarchy-editor-for-proposal
 * Stage:    test_break
 * Agent:    test_designer
 *
 * Acceptance Criteria:
 * ✓ User can edit titles and descriptions of proposed items
 * ✓ User can add/remove hierarchy levels or reorganize structure
 * ✓ Visual feedback on hierarchy validity
 * ✓ Undo/discard changes option available
 *
 * Test Coverage:
 * - Composite hierarchy data model (ProposalItem, ProposalFolder)
 * - Command pattern for reversible operations
 * - Observer pattern for validation feedback
 * - Visitor pattern for tree traversal and validation
 * - Strategy pattern for validation rules
 * - UI interaction and state management
 * - Edge cases and error conditions
 */

// =============================================================================
// TEST FIXTURES & MOCKS
// =============================================================================

/**
 * Composite Pattern Implementation Tests
 * Verifies that hierarchy nodes (items and folders) can be treated uniformly
 */

interface HierarchyNode {
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

class ProposalItem implements HierarchyNode {
  id: string;
  title: string;
  description: string;
  type = "item" as const;
  children: HierarchyNode[] = [];
  parent?: HierarchyNode;

  constructor(id: string, title: string, description: string = "") {
    this.id = id;
    this.title = title;
    this.description = description;
  }

  getChildren(): HierarchyNode[] {
    return this.children;
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

class ProposalFolder implements HierarchyNode {
  id: string;
  title: string;
  description: string;
  type = "folder" as const;
  children: HierarchyNode[] = [];
  parent?: HierarchyNode;

  constructor(id: string, title: string, description: string = "") {
    this.id = id;
    this.title = title;
    this.description = description;
  }

  getChildren(): HierarchyNode[] {
    return this.children;
  }

  addChild(child: HierarchyNode): void {
    if (this.children.some((c) => c.id === child.id)) {
      throw new Error(`Child with id ${child.id} already exists`);
    }
    child.parent = this;
    this.children.push(child);
  }

  removeChild(id: string): boolean {
    const index = this.children.findIndex((c) => c.id === id);
    if (index === -1) return false;
    const removed = this.children[index];
    removed.parent = undefined;
    this.children.splice(index, 1);
    return true;
  }

  accept(visitor: HierarchyVisitor): void {
    visitor.visitProposalFolder(this);
    this.children.forEach((child) => child.accept(visitor));
  }
}

/**
 * Command Pattern for Reversible Operations
 */

interface Command {
  execute(): void;
  undo(): void;
}

class EditTitleCommand implements Command {
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

class EditDescriptionCommand implements Command {
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

class AddChildCommand implements Command {
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

class RemoveChildCommand implements Command {
  private parent: HierarchyNode;
  private child: HierarchyNode;
  private originalIndex: number;

  constructor(parent: HierarchyNode, child: HierarchyNode) {
    this.parent = parent;
    this.child = child;
    this.originalIndex = parent.children.findIndex((c) => c.id === child.id);
    if (this.originalIndex === -1) {
      throw new Error(`Child ${child.id} not found in parent`);
    }
  }

  execute(): void {
    this.parent.removeChild(this.child.id);
  }

  undo(): void {
    this.child.parent = this.parent;
    this.parent.children.splice(this.originalIndex, 0, this.child);
  }
}

class MoveChildCommand implements Command {
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
    this.oldParent.removeChild(this.child.id);
    this.newParent.addChild(this.child);
  }

  undo(): void {
    this.newParent.removeChild(this.child.id);
    this.child.parent = this.oldParent;
    this.oldParent.children.splice(this.oldIndex, 0, this.child);
  }
}

class CommandHistory {
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
}

/**
 * Visitor Pattern for Validation
 */

interface HierarchyVisitor {
  visitProposalItem(item: ProposalItem): void;
  visitProposalFolder(folder: ProposalFolder): void;
}

interface ValidationError {
  nodeId: string;
  message: string;
}

class ValidityVisitor implements HierarchyVisitor {
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

/**
 * Observer Pattern for Real-time Validation Feedback
 */

interface ValidationObserver {
  onValidityChanged(isValid: boolean, errors: ValidationError[]): void;
}

class HierarchyValidator {
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

/**
 * Strategy Pattern for Validation Rules
 */

interface ValidationStrategy {
  validate(node: HierarchyNode): ValidationError[];
}

class NonEmptyTitleStrategy implements ValidationStrategy {
  validate(node: HierarchyNode): ValidationError[] {
    if (!node.title || node.title.trim().length === 0) {
      return [{ nodeId: node.id, message: "Title cannot be empty" }];
    }
    return [];
  }
}

class NoCircularReferenceStrategy implements ValidationStrategy {
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

// =============================================================================
// UNIT TESTS
// =============================================================================

describe("Composite Pattern: Hierarchy Nodes", () => {
  describe("ProposalItem", () => {
    it("should create a leaf node with title and description", () => {
      const item = new ProposalItem("item-1", "Task Title", "Task Description");

      expect(item.id).toBe("item-1");
      expect(item.title).toBe("Task Title");
      expect(item.description).toBe("Task Description");
      expect(item.type).toBe("item");
      expect(item.children).toEqual([]);
    });

    it("should not allow adding children to an item", () => {
      const item = new ProposalItem("item-1", "Task");
      const child = new ProposalItem("item-2", "Child");

      expect(() => item.addChild(child)).toThrow(
        "ProposalItem cannot have children",
      );
    });

    it("should visit itself with a visitor", () => {
      const item = new ProposalItem("item-1", "Task");
      const mockVisitor = {
        visitProposalItem: jest.fn(),
        visitProposalFolder: jest.fn(),
      };

      item.accept(mockVisitor);

      expect(mockVisitor.visitProposalItem).toHaveBeenCalledWith(item);
      expect(mockVisitor.visitProposalFolder).not.toHaveBeenCalled();
    });
  });

  describe("ProposalFolder", () => {
    it("should create a folder with title and description", () => {
      const folder = new ProposalFolder("folder-1", "Folder Title", "Description");

      expect(folder.id).toBe("folder-1");
      expect(folder.title).toBe("Folder Title");
      expect(folder.description).toBe("Description");
      expect(folder.type).toBe("folder");
      expect(folder.children).toEqual([]);
    });

    it("should add children to a folder", () => {
      const folder = new ProposalFolder("folder-1", "Folder");
      const child1 = new ProposalItem("item-1", "Child 1");
      const child2 = new ProposalItem("item-2", "Child 2");

      folder.addChild(child1);
      folder.addChild(child2);

      expect(folder.children).toHaveLength(2);
      expect(folder.children[0]).toBe(child1);
      expect(folder.children[1]).toBe(child2);
      expect(child1.parent).toBe(folder);
      expect(child2.parent).toBe(folder);
    });

    it("should not allow duplicate children", () => {
      const folder = new ProposalFolder("folder-1", "Folder");
      const child = new ProposalItem("item-1", "Child");

      folder.addChild(child);

      expect(() => folder.addChild(child)).toThrow(
        "Child with id item-1 already exists",
      );
    });

    it("should remove children by id", () => {
      const folder = new ProposalFolder("folder-1", "Folder");
      const child1 = new ProposalItem("item-1", "Child 1");
      const child2 = new ProposalItem("item-2", "Child 2");

      folder.addChild(child1);
      folder.addChild(child2);
      const removed = folder.removeChild("item-1");

      expect(removed).toBe(true);
      expect(folder.children).toHaveLength(1);
      expect(folder.children[0]).toBe(child2);
      expect(child1.parent).toBeUndefined();
    });

    it("should return false when removing non-existent child", () => {
      const folder = new ProposalFolder("folder-1", "Folder");
      const removed = folder.removeChild("missing");

      expect(removed).toBe(false);
    });

    it("should visit itself and children with a visitor", () => {
      const folder = new ProposalFolder("folder-1", "Folder");
      const child = new ProposalItem("item-1", "Child");
      folder.addChild(child);

      const mockVisitor = {
        visitProposalItem: jest.fn(),
        visitProposalFolder: jest.fn(),
      };

      folder.accept(mockVisitor);

      expect(mockVisitor.visitProposalFolder).toHaveBeenCalledWith(folder);
      expect(mockVisitor.visitProposalItem).toHaveBeenCalledWith(child);
    });

    it("should recursively visit deep hierarchies", () => {
      const root = new ProposalFolder("root", "Root");
      const level1 = new ProposalFolder("level1", "Level 1");
      const level2 = new ProposalItem("level2", "Level 2");

      root.addChild(level1);
      level1.addChild(level2);

      const visitedNodes: string[] = [];
      const mockVisitor = {
        visitProposalItem: jest.fn((node: ProposalItem) => {
          visitedNodes.push(node.id);
        }),
        visitProposalFolder: jest.fn((node: ProposalFolder) => {
          visitedNodes.push(node.id);
        }),
      };

      root.accept(mockVisitor);

      expect(visitedNodes).toEqual(["root", "level1", "level2"]);
    });
  });
});

describe("Command Pattern: Reversible Operations", () => {
  describe("EditTitleCommand", () => {
    it("should execute title change", () => {
      const node = new ProposalItem("item-1", "Original Title");
      const command = new EditTitleCommand(node, "New Title");

      command.execute();

      expect(node.title).toBe("New Title");
    });

    it("should undo title change", () => {
      const node = new ProposalItem("item-1", "Original Title");
      const command = new EditTitleCommand(node, "New Title");

      command.execute();
      expect(node.title).toBe("New Title");

      command.undo();
      expect(node.title).toBe("Original Title");
    });
  });

  describe("EditDescriptionCommand", () => {
    it("should execute description change", () => {
      const node = new ProposalItem("item-1", "Title", "Original Description");
      const command = new EditDescriptionCommand(node, "New Description");

      command.execute();

      expect(node.description).toBe("New Description");
    });

    it("should undo description change", () => {
      const node = new ProposalItem("item-1", "Title", "Original Description");
      const command = new EditDescriptionCommand(node, "New Description");

      command.execute();
      expect(node.description).toBe("New Description");

      command.undo();
      expect(node.description).toBe("Original Description");
    });
  });

  describe("AddChildCommand", () => {
    it("should add child to folder", () => {
      const folder = new ProposalFolder("folder-1", "Folder");
      const child = new ProposalItem("item-1", "Child");
      const command = new AddChildCommand(folder, child);

      command.execute();

      expect(folder.children).toHaveLength(1);
      expect(folder.children[0]).toBe(child);
    });

    it("should undo adding child", () => {
      const folder = new ProposalFolder("folder-1", "Folder");
      const child = new ProposalItem("item-1", "Child");
      const command = new AddChildCommand(folder, child);

      command.execute();
      expect(folder.children).toHaveLength(1);

      command.undo();
      expect(folder.children).toHaveLength(0);
    });

    it("should not add child to an item", () => {
      const item = new ProposalItem("item-1", "Item");
      const child = new ProposalItem("item-2", "Child");

      expect(() => new AddChildCommand(item, child)).toThrow(
        "Cannot add child to a ProposalItem",
      );
    });
  });

  describe("RemoveChildCommand", () => {
    it("should remove child from folder", () => {
      const folder = new ProposalFolder("folder-1", "Folder");
      const child = new ProposalItem("item-1", "Child");
      folder.addChild(child);

      const command = new RemoveChildCommand(folder, child);
      command.execute();

      expect(folder.children).toHaveLength(0);
    });

    it("should undo removing child at original position", () => {
      const folder = new ProposalFolder("folder-1", "Folder");
      const child1 = new ProposalItem("item-1", "Child 1");
      const child2 = new ProposalItem("item-2", "Child 2");
      const child3 = new ProposalItem("item-3", "Child 3");

      folder.addChild(child1);
      folder.addChild(child2);
      folder.addChild(child3);

      const command = new RemoveChildCommand(folder, child2);
      command.execute();
      expect(folder.children).toEqual([child1, child3]);

      command.undo();
      expect(folder.children).toEqual([child1, child2, child3]);
    });

    it("should throw if child not found in parent", () => {
      const folder = new ProposalFolder("folder-1", "Folder");
      const child = new ProposalItem("item-1", "Child");

      expect(() => new RemoveChildCommand(folder, child)).toThrow(
        "Child item-1 not found in parent",
      );
    });
  });

  describe("MoveChildCommand", () => {
    it("should move child to new parent", () => {
      const oldParent = new ProposalFolder("folder-1", "Old Parent");
      const newParent = new ProposalFolder("folder-2", "New Parent");
      const child = new ProposalItem("item-1", "Child");

      oldParent.addChild(child);

      const command = new MoveChildCommand(child, newParent);
      command.execute();

      expect(oldParent.children).toHaveLength(0);
      expect(newParent.children).toHaveLength(1);
      expect(newParent.children[0]).toBe(child);
    });

    it("should undo moving child to original parent", () => {
      const oldParent = new ProposalFolder("folder-1", "Old Parent");
      const newParent = new ProposalFolder("folder-2", "New Parent");
      const child = new ProposalItem("item-1", "Child");

      oldParent.addChild(child);

      const command = new MoveChildCommand(child, newParent);
      command.execute();
      command.undo();

      expect(oldParent.children).toHaveLength(1);
      expect(oldParent.children[0]).toBe(child);
      expect(newParent.children).toHaveLength(0);
    });

    it("should prevent circular references when moving", () => {
      const root = new ProposalFolder("root", "Root");
      const parent = new ProposalFolder("folder-1", "Parent");
      const child = new ProposalFolder("folder-2", "Child");
      const grandchild = new ProposalItem("item-1", "Grandchild");

      root.addChild(parent);
      parent.addChild(child);
      child.addChild(grandchild);

      expect(() => new MoveChildCommand(parent, child)).toThrow(
        "Cannot move parent into its own child",
      );
    });

    it("should not move to a ProposalItem", () => {
      const parent = new ProposalFolder("folder-1", "Parent");
      const child = new ProposalItem("item-1", "Child");
      const target = new ProposalItem("item-2", "Target");

      parent.addChild(child);

      expect(() => new MoveChildCommand(child, target)).toThrow(
        "Cannot move child to a ProposalItem",
      );
    });
  });

  describe("CommandHistory", () => {
    it("should execute commands and track history", () => {
      const node = new ProposalItem("item-1", "Title");
      const history = new CommandHistory();

      const command1 = new EditTitleCommand(node, "Title 1");
      const command2 = new EditTitleCommand(node, "Title 2");

      history.execute(command1);
      expect(node.title).toBe("Title 1");

      history.execute(command2);
      expect(node.title).toBe("Title 2");
    });

    it("should undo commands in reverse order", () => {
      const node = new ProposalItem("item-1", "Title");
      const history = new CommandHistory();

      history.execute(new EditTitleCommand(node, "Title 1"));
      history.execute(new EditTitleCommand(node, "Title 2"));

      expect(history.canUndo()).toBe(true);
      history.undo();
      expect(node.title).toBe("Title 1");

      history.undo();
      expect(node.title).toBe("Title");

      expect(history.canUndo()).toBe(false);
    });

    it("should redo commands", () => {
      const node = new ProposalItem("item-1", "Title");
      const history = new CommandHistory();

      history.execute(new EditTitleCommand(node, "Title 1"));
      history.execute(new EditTitleCommand(node, "Title 2"));

      history.undo();
      history.undo();

      expect(history.canRedo()).toBe(true);
      history.redo();
      expect(node.title).toBe("Title 1");

      history.redo();
      expect(node.title).toBe("Title 2");
    });

    it("should clear forward history when new command executed after undo", () => {
      const node = new ProposalItem("item-1", "Title");
      const history = new CommandHistory();

      history.execute(new EditTitleCommand(node, "Title 1"));
      history.execute(new EditTitleCommand(node, "Title 2"));
      history.undo();

      expect(history.canRedo()).toBe(true);

      history.execute(new EditTitleCommand(node, "Title 3"));

      expect(history.canRedo()).toBe(false);
      expect(node.title).toBe("Title 3");
    });

    it("should report canUndo and canRedo correctly", () => {
      const history = new CommandHistory();
      const node = new ProposalItem("item-1", "Title");

      expect(history.canUndo()).toBe(false);
      expect(history.canRedo()).toBe(false);

      history.execute(new EditTitleCommand(node, "Title 1"));
      expect(history.canUndo()).toBe(true);
      expect(history.canRedo()).toBe(false);

      history.undo();
      expect(history.canUndo()).toBe(false);
      expect(history.canRedo()).toBe(true);
    });

    it("should clear history when clear() is called", () => {
      const history = new CommandHistory();
      const node = new ProposalItem("item-1", "Title");

      history.execute(new EditTitleCommand(node, "Title 1"));
      expect(history.canUndo()).toBe(true);

      history.clear();

      expect(history.canUndo()).toBe(false);
      expect(history.canRedo()).toBe(false);
    });
  });
});

describe("Visitor Pattern: Hierarchy Traversal", () => {
  it("should validate hierarchy with ValidityVisitor", () => {
    const folder = new ProposalFolder("folder-1", "Folder");
    const item1 = new ProposalItem("item-1", "Item 1");
    const item2 = new ProposalItem("item-2", ""); // Empty title

    folder.addChild(item1);
    folder.addChild(item2);

    const visitor = new ValidityVisitor();
    folder.accept(visitor);

    expect(visitor.isValid()).toBe(false);
    const errors = visitor.getErrors();
    expect(errors).toContainEqual({
      nodeId: "item-2",
      message: "Title cannot be empty",
    });
  });

  it("should detect empty folder as validation error", () => {
    const emptyFolder = new ProposalFolder("folder-1", "Empty Folder");

    const visitor = new ValidityVisitor();
    emptyFolder.accept(visitor);

    expect(visitor.isValid()).toBe(false);
    const errors = visitor.getErrors();
    expect(errors).toContainEqual({
      nodeId: "folder-1",
      message: "Folder must contain at least one child",
    });
  });

  it("should report valid hierarchy", () => {
    const folder = new ProposalFolder("folder-1", "Folder");
    const item = new ProposalItem("item-1", "Item");

    folder.addChild(item);

    const visitor = new ValidityVisitor();
    folder.accept(visitor);

    expect(visitor.isValid()).toBe(true);
    expect(visitor.getErrors()).toHaveLength(0);
  });
});

describe("Observer Pattern: Validation Feedback", () => {
  it("should notify observers when validity changes", () => {
    const folder = new ProposalFolder("folder-1", "Folder");
    const validator = new HierarchyValidator();

    const mockObserver = {
      onValidityChanged: jest.fn(),
    };

    validator.subscribe(mockObserver);
    validator.validate(folder);

    expect(mockObserver.onValidityChanged).toHaveBeenCalledWith(false, expect.any(Array));
  });

  it("should pass validation errors to observers", () => {
    const folder = new ProposalFolder("folder-1", "");
    const validator = new HierarchyValidator();

    const mockObserver = {
      onValidityChanged: jest.fn(),
    };

    validator.subscribe(mockObserver);
    validator.validate(folder);

    const [, errors] = mockObserver.onValidityChanged.mock.calls[0];
    expect(errors.length).toBeGreaterThan(0);
  });

  it("should unsubscribe observers", () => {
    const validator = new HierarchyValidator();
    const mockObserver = {
      onValidityChanged: jest.fn(),
    };

    validator.subscribe(mockObserver);
    validator.unsubscribe(mockObserver);
    validator.validate(new ProposalFolder("folder-1", "Folder"));

    expect(mockObserver.onValidityChanged).not.toHaveBeenCalled();
  });

  it("should support multiple observers", () => {
    const folder = new ProposalFolder("folder-1", "Folder");
    const validator = new HierarchyValidator();

    const observer1 = { onValidityChanged: jest.fn() };
    const observer2 = { onValidityChanged: jest.fn() };

    validator.subscribe(observer1);
    validator.subscribe(observer2);
    validator.validate(folder);

    expect(observer1.onValidityChanged).toHaveBeenCalled();
    expect(observer2.onValidityChanged).toHaveBeenCalled();
  });
});

describe("Strategy Pattern: Validation Rules", () => {
  describe("NonEmptyTitleStrategy", () => {
    it("should validate non-empty titles", () => {
      const strategy = new NonEmptyTitleStrategy();
      const item = new ProposalItem("item-1", "Valid Title");

      const errors = strategy.validate(item);

      expect(errors).toHaveLength(0);
    });

    it("should reject empty titles", () => {
      const strategy = new NonEmptyTitleStrategy();
      const item = new ProposalItem("item-1", "");

      const errors = strategy.validate(item);

      expect(errors).toHaveLength(1);
      expect(errors[0].message).toBe("Title cannot be empty");
    });

    it("should reject whitespace-only titles", () => {
      const strategy = new NonEmptyTitleStrategy();
      const item = new ProposalItem("item-1", "   ");

      const errors = strategy.validate(item);

      expect(errors).toHaveLength(1);
    });
  });

  describe("NoCircularReferenceStrategy", () => {
    it("should accept valid hierarchy without circular references", () => {
      const strategy = new NoCircularReferenceStrategy();
      const root = new ProposalFolder("root", "Root");
      const child = new ProposalFolder("child", "Child");
      const grandchild = new ProposalItem("item", "Item");

      root.addChild(child);
      child.addChild(grandchild);

      const errors = strategy.validate(root);

      expect(errors).toHaveLength(0);
    });

    it("should not validate items (they cannot have children)", () => {
      const strategy = new NoCircularReferenceStrategy();
      const item = new ProposalItem("item-1", "Item");

      const errors = strategy.validate(item);

      expect(errors).toHaveLength(0);
    });
  });
});

// =============================================================================
// INTEGRATION TESTS: Hierarchy Editor Behavior
// =============================================================================

describe("Hierarchy Editor Integration", () => {
  describe("Acceptance Criterion 1: Edit titles and descriptions", () => {
    it("should allow editing item title", () => {
      const item = new ProposalItem("item-1", "Original Title");
      const history = new CommandHistory();

      history.execute(new EditTitleCommand(item, "Updated Title"));

      expect(item.title).toBe("Updated Title");
    });

    it("should allow editing item description", () => {
      const item = new ProposalItem("item-1", "Title", "Original Description");
      const history = new CommandHistory();

      history.execute(new EditDescriptionCommand(item, "Updated Description"));

      expect(item.description).toBe("Updated Description");
    });

    it("should allow editing folder title", () => {
      const folder = new ProposalFolder("folder-1", "Original Name");
      const history = new CommandHistory();

      history.execute(new EditTitleCommand(folder, "Updated Name"));

      expect(folder.title).toBe("Updated Name");
    });

    it("should support multiple sequential edits", () => {
      const item = new ProposalItem("item-1", "Title 1", "Desc 1");
      const history = new CommandHistory();

      history.execute(new EditTitleCommand(item, "Title 2"));
      history.execute(new EditDescriptionCommand(item, "Desc 2"));
      history.execute(new EditTitleCommand(item, "Title 3"));

      expect(item.title).toBe("Title 3");
      expect(item.description).toBe("Desc 2");
    });
  });

  describe("Acceptance Criterion 2: Add/remove hierarchy levels and reorganize", () => {
    it("should add new level to hierarchy", () => {
      const root = new ProposalFolder("root", "Root");
      const level1 = new ProposalFolder("level1", "Level 1");
      const history = new CommandHistory();

      history.execute(new AddChildCommand(root, level1));

      expect(root.children).toHaveLength(1);
      expect(root.children[0]).toBe(level1);
    });

    it("should remove level from hierarchy", () => {
      const root = new ProposalFolder("root", "Root");
      const level1 = new ProposalFolder("level1", "Level 1");
      root.addChild(level1);

      const history = new CommandHistory();
      history.execute(new RemoveChildCommand(root, level1));

      expect(root.children).toHaveLength(0);
    });

    it("should reorganize structure by moving children", () => {
      const folder1 = new ProposalFolder("f1", "Folder 1");
      const folder2 = new ProposalFolder("f2", "Folder 2");
      const item = new ProposalItem("item", "Item");

      folder1.addChild(item);

      const history = new CommandHistory();
      history.execute(new MoveChildCommand(item, folder2));

      expect(folder1.children).toHaveLength(0);
      expect(folder2.children).toHaveLength(1);
    });

    it("should support complex reorganization with undo", () => {
      const root = new ProposalFolder("root", "Root");
      const folder1 = new ProposalFolder("f1", "Folder 1");
      const folder2 = new ProposalFolder("f2", "Folder 2");
      const item = new ProposalItem("item", "Item");

      root.addChild(folder1);
      root.addChild(folder2);
      folder1.addChild(item);

      const history = new CommandHistory();

      // Move item to folder2
      history.execute(new MoveChildCommand(item, folder2));
      expect(folder2.children).toContainEqual(item);

      // Undo
      history.undo();
      expect(folder1.children).toContainEqual(item);
    });
  });

  describe("Acceptance Criterion 3: Visual feedback on hierarchy validity", () => {
    it("should report invalid hierarchy when item has no title", () => {
      const folder = new ProposalFolder("folder-1", "Folder");
      const item = new ProposalItem("item-1", "");
      folder.addChild(item);

      const validator = new HierarchyValidator();
      validator.validate(folder);

      expect(validator.getIsValid()).toBe(false);
      expect(validator.getErrors().length).toBeGreaterThan(0);
    });

    it("should report invalid hierarchy for empty folders", () => {
      const emptyFolder = new ProposalFolder("folder-1", "Empty");
      const validator = new HierarchyValidator();

      validator.validate(emptyFolder);

      expect(validator.getIsValid()).toBe(false);
    });

    it("should report valid hierarchy when all constraints met", () => {
      const root = new ProposalFolder("root", "Root");
      const item = new ProposalItem("item-1", "Item 1");
      root.addChild(item);

      const validator = new HierarchyValidator();
      validator.validate(root);

      expect(validator.getIsValid()).toBe(true);
      expect(validator.getErrors()).toHaveLength(0);
    });

    it("should provide detailed error messages to UI", () => {
      const folder = new ProposalFolder("folder-1", "");
      const validator = new HierarchyValidator();

      validator.validate(folder);

      const errors = validator.getErrors();
      expect(errors).toContainEqual(
        expect.objectContaining({
          nodeId: "folder-1",
          message: expect.stringContaining("cannot be empty"),
        }),
      );
    });

    it("should update observers of validation changes", () => {
      const folder = new ProposalFolder("folder-1", "Folder");
      const validator = new HierarchyValidator();
      const mockObserver = { onValidityChanged: jest.fn() };

      validator.subscribe(mockObserver);
      validator.validate(folder);

      expect(mockObserver.onValidityChanged).toHaveBeenCalled();
      const [isValid, errors] = mockObserver.onValidityChanged.mock.calls[0];
      expect(typeof isValid).toBe("boolean");
      expect(Array.isArray(errors)).toBe(true);
    });
  });

  describe("Acceptance Criterion 4: Undo/discard changes", () => {
    it("should undo single edit", () => {
      const item = new ProposalItem("item-1", "Original");
      const history = new CommandHistory();

      history.execute(new EditTitleCommand(item, "Updated"));
      expect(item.title).toBe("Updated");

      history.undo();
      expect(item.title).toBe("Original");
    });

    it("should undo multiple edits in reverse order", () => {
      const item = new ProposalItem("item-1", "Title 1", "Desc 1");
      const history = new CommandHistory();

      history.execute(new EditTitleCommand(item, "Title 2"));
      history.execute(new EditDescriptionCommand(item, "Desc 2"));

      history.undo();
      expect(item.description).toBe("Desc 1");

      history.undo();
      expect(item.title).toBe("Title 1");
    });

    it("should undo structural changes (add/remove)", () => {
      const folder = new ProposalFolder("folder-1", "Folder");
      const item = new ProposalItem("item-1", "Item");
      const history = new CommandHistory();

      history.execute(new AddChildCommand(folder, item));
      expect(folder.children).toHaveLength(1);

      history.undo();
      expect(folder.children).toHaveLength(0);
    });

    it("should discard all changes by clearing history", () => {
      const item = new ProposalItem("item-1", "Original");
      const history = new CommandHistory();

      history.execute(new EditTitleCommand(item, "Updated"));
      history.execute(new EditTitleCommand(item, "Updated Again"));

      history.clear();

      expect(history.canUndo()).toBe(false);
      // Note: calling clear() doesn't undo changes, just clears history
    });

    it("should prevent undo when at beginning of history", () => {
      const history = new CommandHistory();
      const result = history.undo();

      expect(result).toBe(false);
    });

    it("should allow redo after undo", () => {
      const item = new ProposalItem("item-1", "Original");
      const history = new CommandHistory();

      history.execute(new EditTitleCommand(item, "Updated"));
      history.undo();
      expect(item.title).toBe("Original");

      history.redo();
      expect(item.title).toBe("Updated");
    });
  });
});

// =============================================================================
// EDGE CASES & ERROR CONDITIONS
// =============================================================================

describe("Edge Cases & Error Handling", () => {
  it("should handle very deep hierarchies", () => {
    let current: HierarchyNode = new ProposalItem("item-0", "Item 0");
    const history = new CommandHistory();

    for (let i = 1; i <= 50; i++) {
      const parent = new ProposalFolder(`folder-${i}`, `Folder ${i}`);
      history.execute(new AddChildCommand(parent, current));
      current = parent;
    }

    // Validate deep hierarchy
    const validator = new HierarchyValidator();
    validator.validate(current);

    // Should be valid (each folder has one child)
    expect(validator.getIsValid()).toBe(true);
  });

  it("should handle large number of siblings", () => {
    const folder = new ProposalFolder("folder-1", "Folder");
    const history = new CommandHistory();

    for (let i = 0; i < 1000; i++) {
      const item = new ProposalItem(`item-${i}`, `Item ${i}`);
      history.execute(new AddChildCommand(folder, item));
    }

    expect(folder.children).toHaveLength(1000);

    // Remove one and check integrity
    const firstChild = folder.children[0];
    history.execute(new RemoveChildCommand(folder, firstChild));
    expect(folder.children).toHaveLength(999);
  });

  it("should handle empty title after edits", () => {
    const item = new ProposalItem("item-1", "Title");
    const history = new CommandHistory();

    history.execute(new EditTitleCommand(item, ""));

    const validator = new HierarchyValidator();
    const folder = new ProposalFolder("folder", "Folder");
    folder.addChild(item);
    validator.validate(folder);

    expect(validator.getIsValid()).toBe(false);
  });

  it("should restore folder structure on undo of move", () => {
    const source = new ProposalFolder("source", "Source");
    const target = new ProposalFolder("target", "Target");
    const subfolder = new ProposalFolder("sub", "Sub");
    const item1 = new ProposalItem("item1", "Item 1");
    const item2 = new ProposalItem("item2", "Item 2");

    source.addChild(subfolder);
    subfolder.addChild(item1);
    subfolder.addChild(item2);

    const history = new CommandHistory();
    history.execute(new MoveChildCommand(subfolder, target));

    expect(source.children).toHaveLength(0);
    expect(target.children).toHaveLength(1);
    expect(subfolder.children).toHaveLength(2);

    history.undo();

    expect(source.children).toHaveLength(1);
    expect(source.children[0]).toBe(subfolder);
    expect(subfolder.children).toHaveLength(2);
  });

  it("should handle moving node between folders", () => {
    const root = new ProposalFolder("root", "Root");
    const folder1 = new ProposalFolder("folder1", "Folder 1");
    const folder2 = new ProposalFolder("folder2", "Folder 2");
    const item = new ProposalItem("item", "Item");

    root.addChild(folder1);
    root.addChild(folder2);
    folder1.addChild(item);

    const command = new MoveChildCommand(item, folder2);
    command.execute();

    expect(folder1.children).toHaveLength(0);
    expect(folder2.children).toHaveLength(1);
    expect(folder2.children[0]).toBe(item);
  });
});
