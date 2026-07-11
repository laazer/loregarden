/**
 * Hierarchy Editor Component
 *
 * Interactive UI for editing and restructuring AI-generated hierarchy proposals.
 * Supports editing titles/descriptions, adding/removing levels, drag-drop reorganization,
 * visual validation feedback, and undo/redo with discard options.
 */

import { useEffect, useState, useCallback, useMemo } from "react";
import {
  HierarchyNode,
  ProposalItem,
  ProposalFolder,
  CommandHistory,
  EditTitleCommand,
  EditDescriptionCommand,
  AddChildCommand,
  RemoveChildCommand,
  MoveChildCommand,
  HierarchyValidator,
  ValidationError,
  ValidationObserver,
} from "./models";
import styles from "./HierarchyEditor.module.css";

export interface HierarchyEditorProps {
  initialHierarchy?: HierarchyNode[];
  onFinalize?: (hierarchy: HierarchyNode[]) => Promise<void>;
  onDiscard?: () => void;
  isLoading?: boolean;
}

interface NodeEditorState {
  expandedNodeIds: Set<string>;
  selectedNodeId: string | null;
  editingNodeId: string | null;
}

function nodeKey(node: HierarchyNode): string {
  return `${node.type}-${node.id}`;
}

function cloneNode(node: HierarchyNode): HierarchyNode {
  if (node.type === "item") {
    return new ProposalItem(node.id, node.title, node.description);
  }
  const folder = new ProposalFolder(node.id, node.title, node.description);
  node.children.forEach((child) => {
    folder.addChild(cloneNode(child));
  });
  return folder;
}

function generateNodeId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

export function HierarchyEditor({
  initialHierarchy = [],
  onFinalize,
  onDiscard,
  isLoading = false,
}: HierarchyEditorProps) {
  const [rootNodes, setRootNodes] = useState<HierarchyNode[]>(
    initialHierarchy.map(cloneNode),
  );
  const [history] = useState(() => new CommandHistory());
  const [, setValidationState] = useState<{
    isValid: boolean;
    errors: ValidationError[];
  }>({
    isValid: true,
    errors: [],
  });
  const [editorState, setEditorState] = useState<NodeEditorState>({
    expandedNodeIds: new Set(),
    selectedNodeId: null,
    editingNodeId: null,
  });
  const [validationErrors, setValidationErrors] = useState<
    Map<string, ValidationError[]>
  >(new Map());

  const validator = useMemo(() => {
    const v = new HierarchyValidator();
    const observer: ValidationObserver = {
      onValidityChanged(isValid, errors) {
        setValidationState({ isValid, errors });
        const errorsByNodeId = new Map<string, ValidationError[]>();
        errors.forEach((err) => {
          const existing = errorsByNodeId.get(err.nodeId) || [];
          existing.push(err);
          errorsByNodeId.set(err.nodeId, existing);
        });
        setValidationErrors(errorsByNodeId);
      },
    };
    v.subscribe(observer);
    return v;
  }, []);

  useEffect(() => {
    if (rootNodes.length === 0) return;
    rootNodes.forEach((root) => validator.validate(root));
  }, [rootNodes, validator]);

  const handleEditTitle = useCallback(
    (node: HierarchyNode, newTitle: string) => {
      const command = new EditTitleCommand(node, newTitle);
      history.execute(command);
      setRootNodes([...rootNodes]);
    },
    [history, rootNodes],
  );

  const handleEditDescription = useCallback(
    (node: HierarchyNode, newDescription: string) => {
      const command = new EditDescriptionCommand(node, newDescription);
      history.execute(command);
      setRootNodes([...rootNodes]);
    },
    [history, rootNodes],
  );

  const handleAddChild = useCallback(
    (parent: HierarchyNode, type: "item" | "folder") => {
      if (parent.type === "item") return;
      const childId = generateNodeId(type);
      const child =
        type === "item"
          ? new ProposalItem(childId, "New Item")
          : new ProposalFolder(childId, "New Folder");

      const command = new AddChildCommand(parent, child);
      history.execute(command);
      setRootNodes([...rootNodes]);
      setEditorState((s) => ({
        ...s,
        expandedNodeIds: new Set([...s.expandedNodeIds, parent.id]),
        selectedNodeId: childId,
        editingNodeId: childId,
      }));
    },
    [history, rootNodes],
  );

  const handleRemoveNode = useCallback(
    (node: HierarchyNode) => {
      if (!node.parent) return;
      const command = new RemoveChildCommand(node.parent, node);
      history.execute(command);
      setRootNodes([...rootNodes]);
      setEditorState((s) => ({
        ...s,
        selectedNodeId: null,
        editingNodeId: null,
      }));
    },
    [history, rootNodes],
  );

  const handleMoveNode = useCallback(
    (node: HierarchyNode, newParent: HierarchyNode) => {
      if (!node.parent || newParent.type === "item") return;
      try {
        const command = new MoveChildCommand(node, newParent);
        history.execute(command);
        setRootNodes([...rootNodes]);
      } catch (e) {
        // Invalid move (circular reference, etc.)
      }
    },
    [history, rootNodes],
  );

  const handleUndo = useCallback(() => {
    history.undo();
    setRootNodes([...rootNodes]);
  }, [history, rootNodes]);

  const handleRedo = useCallback(() => {
    history.redo();
    setRootNodes([...rootNodes]);
  }, [history, rootNodes]);

  const handleDiscardChanges = useCallback(() => {
    history.clear();
    setRootNodes(initialHierarchy.map(cloneNode));
    setEditorState({
      expandedNodeIds: new Set(),
      selectedNodeId: null,
      editingNodeId: null,
    });
    onDiscard?.();
  }, [history, initialHierarchy, onDiscard]);

  const handleFinalize = useCallback(async () => {
    if (!onFinalize) return;
    try {
      await onFinalize(rootNodes);
      history.clear();
      setRootNodes([]);
    } catch (e) {
      // Error handled by parent
    }
  }, [rootNodes, onFinalize, history]);

  const toggleExpanded = useCallback((nodeId: string) => {
    setEditorState((s) => {
      const expanded = new Set(s.expandedNodeIds);
      if (expanded.has(nodeId)) {
        expanded.delete(nodeId);
      } else {
        expanded.add(nodeId);
      }
      return { ...s, expandedNodeIds: expanded };
    });
  }, []);

  const selectNode = useCallback((nodeId: string | null) => {
    setEditorState((s) => ({
      ...s,
      selectedNodeId: nodeId,
      editingNodeId: null,
    }));
  }, []);

  const startEditing = useCallback((nodeId: string) => {
    setEditorState((s) => ({
      ...s,
      selectedNodeId: nodeId,
      editingNodeId: nodeId,
    }));
  }, []);

  const stopEditing = useCallback(() => {
    setEditorState((s) => ({
      ...s,
      editingNodeId: null,
    }));
  }, []);

  const findNodeById = (id: string, nodes: HierarchyNode[]): HierarchyNode | null => {
    for (const node of nodes) {
      if (node.id === id) return node;
      const found = findNodeById(id, node.children);
      if (found) return found;
    }
    return null;
  };

  return (
    <div className={styles.hierarchyEditor}>
      <div className={styles.toolbar}>
        <div className={styles.buttonGroup}>
          <button
            onClick={handleUndo}
            disabled={!history.canUndo() || isLoading}
            className={styles.button}
            title="Undo (Cmd+Z)"
          >
            ↶ Undo
          </button>
          <button
            onClick={handleRedo}
            disabled={!history.canRedo() || isLoading}
            className={styles.button}
            title="Redo (Cmd+Shift+Z)"
          >
            ↷ Redo
          </button>
        </div>

        <div className={styles.buttonGroup}>
          <button
            onClick={handleDiscardChanges}
            disabled={isLoading}
            className={`${styles.button} ${styles.secondary}`}
          >
            Discard
          </button>
          <button
            onClick={handleFinalize}
            disabled={!validator.getIsValid() || isLoading}
            className={`${styles.button} ${styles.primary}`}
          >
            {isLoading ? "Finalizing..." : "Finalize"}
          </button>
        </div>
      </div>

      {validationErrors.size > 0 && (
        <div className={styles.validationErrors}>
          <strong>Validation Errors:</strong>
          <ul>
            {Array.from(validationErrors.values()).flatMap((errs) =>
              errs.map((err, i) => (
                <li key={`${err.nodeId}-${i}`}>{err.message}</li>
              )),
            )}
          </ul>
        </div>
      )}

      <div className={styles.hierarchyTree}>
        {rootNodes.map((node) => (
          <HierarchyNodeEditor
            key={nodeKey(node)}
            node={node}
            isExpanded={editorState.expandedNodeIds.has(node.id)}
            isSelected={editorState.selectedNodeId === node.id}
            isEditing={editorState.editingNodeId === node.id}
            errors={validationErrors.get(node.id) || []}
            onToggleExpanded={toggleExpanded}
            onSelect={selectNode}
            onEditTitle={handleEditTitle}
            onEditDescription={handleEditDescription}
            onAddChild={handleAddChild}
            onRemove={handleRemoveNode}
            onMove={handleMoveNode}
            onStartEditing={startEditing}
            onStopEditing={stopEditing}
            depth={0}
          />
        ))}
      </div>
    </div>
  );
}

interface HierarchyNodeEditorProps {
  node: HierarchyNode;
  isExpanded: boolean;
  isSelected: boolean;
  isEditing: boolean;
  errors: ValidationError[];
  onToggleExpanded: (nodeId: string) => void;
  onSelect: (nodeId: string | null) => void;
  onEditTitle: (node: HierarchyNode, title: string) => void;
  onEditDescription: (node: HierarchyNode, description: string) => void;
  onAddChild: (node: HierarchyNode, type: "item" | "folder") => void;
  onRemove: (node: HierarchyNode) => void;
  onMove: (node: HierarchyNode, parent: HierarchyNode) => void;
  onStartEditing: (nodeId: string) => void;
  onStopEditing: () => void;
  depth: number;
}

function HierarchyNodeEditor({
  node,
  isExpanded,
  isSelected,
  isEditing,
  errors,
  onToggleExpanded,
  onSelect,
  onEditTitle,
  onEditDescription,
  onAddChild,
  onRemove,
  onMove,
  onStartEditing,
  onStopEditing,
  depth,
}: HierarchyNodeEditorProps) {
  const hasChildren = node.children.length > 0;
  const [titleValue, setTitleValue] = useState(node.title);
  const [descriptionValue, setDescriptionValue] = useState(node.description);

  useEffect(() => {
    setTitleValue(node.title);
  }, [node.title]);

  useEffect(() => {
    setDescriptionValue(node.description);
  }, [node.description]);

  const handleTitleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setTitleValue(e.target.value);
  };

  const handleTitleBlur = () => {
    if (titleValue.trim() !== node.title.trim()) {
      onEditTitle(node, titleValue);
    }
    onStopEditing();
  };

  const handleDescriptionChange = (
    e: React.ChangeEvent<HTMLTextAreaElement>,
  ) => {
    setDescriptionValue(e.target.value);
  };

  const handleDescriptionBlur = () => {
    if (descriptionValue !== node.description) {
      onEditDescription(node, descriptionValue);
    }
  };

  return (
    <div className={styles.nodeWrapper} style={{ marginLeft: `${depth * 1.5}rem` }}>
      <div
        className={`${styles.node} ${isSelected ? styles.selected : ""} ${
          errors.length > 0 ? styles.error : ""
        }`}
      >
        <div className={styles.nodeHeader}>
          {hasChildren && (
            <button
              onClick={() => onToggleExpanded(node.id)}
              className={styles.expandButton}
            >
              {isExpanded ? "▼" : "▶"}
            </button>
          )}
          {!hasChildren && <span className={styles.expandPlaceholder} />}

          <span className={styles.nodeType}>{node.type}</span>

          <div className={styles.nodeContent} onClick={() => onSelect(node.id)}>
            {isEditing ? (
              <input
                autoFocus
                type="text"
                value={titleValue}
                onChange={handleTitleChange}
                onBlur={handleTitleBlur}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleTitleBlur();
                  if (e.key === "Escape") onStopEditing();
                }}
                className={styles.titleInput}
              />
            ) : (
              <div
                className={styles.title}
                onDoubleClick={() => onStartEditing(node.id)}
              >
                {node.title || "(empty)"}
              </div>
            )}
          </div>

          {node.parent && (
            <button
              onClick={() => onRemove(node)}
              className={`${styles.actionButton} ${styles.danger}`}
              title="Remove node"
            >
              ✕
            </button>
          )}
        </div>

        {node.description && (
          <div className={styles.nodeDescription}>
            {node.description}
          </div>
        )}

        {errors.length > 0 && (
          <div className={styles.errorMessages}>
            {errors.map((err, i) => (
              <div key={i} className={styles.errorMessage}>
                {err.message}
              </div>
            ))}
          </div>
        )}

        {node.type === "folder" && (
          <div className={styles.nodeActions}>
            <button
              onClick={() => onAddChild(node, "item")}
              className={styles.actionButton}
              title="Add item"
            >
              + Item
            </button>
            <button
              onClick={() => onAddChild(node, "folder")}
              className={styles.actionButton}
              title="Add folder"
            >
              + Folder
            </button>
          </div>
        )}
      </div>

      {isExpanded && hasChildren && (
        <div className={styles.children}>
          {node.children.map((child) => (
            <HierarchyNodeEditor
              key={`${child.type}-${child.id}`}
              node={child}
              isExpanded={false}
              isSelected={false}
              isEditing={false}
              errors={[]}
              onToggleExpanded={onToggleExpanded}
              onSelect={onSelect}
              onEditTitle={onEditTitle}
              onEditDescription={onEditDescription}
              onAddChild={onAddChild}
              onRemove={onRemove}
              onMove={onMove}
              onStartEditing={onStartEditing}
              onStopEditing={onStopEditing}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}
