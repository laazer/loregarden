import type { LocalModel } from "../../api/diskTypes";
import { formatDiskBytes, MODEL_SOURCE_LABELS } from "../../lib/diskUsage";
import { formatRelativeAge } from "../../lib/timestamps";
import { Button } from "../ui/Button";

interface LocalModelsTableProps {
  models: LocalModel[];
  /** Opens the confirm dialog, which holds focus and the in-flight state. */
  onDelete: (model: LocalModel) => void;
}

/**
 * "Which model can I delete to get space back?": one row per model, largest
 * first (the server's order), with what deleting it frees beside its size,
 * because Ollama models share files and an LM Studio link frees nothing.
 */
export function LocalModelsTable({ models, onDelete }: LocalModelsTableProps) {
  return (
    <div className="disk-entries-scroll">
      <table className="disk-entries-table disk-models-table">
        <caption className="disk-sr-only">Downloaded models</caption>
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col">App</th>
            <th scope="col" className="disk-col-num" aria-sort="descending">
              Size
            </th>
            <th scope="col" className="disk-col-num">
              Frees
            </th>
            <th scope="col">Changed</th>
            <th scope="col">
              <span className="disk-sr-only">Delete</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {models.map((model) => (
            <tr key={model.id}>
              <th scope="row" className="disk-col-name">
                <span className="disk-model-name">{model.name}</span>
                {model.broken ? (
                  <span className="disk-model-tag is-broken">Broken link</span>
                ) : model.links_to ? (
                  <span className="disk-model-tag">
                    Link to {MODEL_SOURCE_LABELS[model.links_to]}
                  </span>
                ) : null}
              </th>
              <td data-label="App">{MODEL_SOURCE_LABELS[model.source]}</td>
              <td className="disk-col-num" data-label="Size">
                {formatDiskBytes(model.size_bytes)}
              </td>
              <td className="disk-col-num" data-label="Frees">
                {formatDiskBytes(model.frees_bytes)}
              </td>
              <td data-label="Changed">
                {formatRelativeAge(model.modified_at)}
              </td>
              <td className="disk-model-action">
                <Button
                  variant="secondary"
                  compact
                  aria-label={`Delete ${model.name}`}
                  onClick={() => onDelete(model)}
                >
                  Delete…
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
