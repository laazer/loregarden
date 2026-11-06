import { Link } from "react-router-dom";

import { useDockerCapacity } from "../../hooks/useDockerCapacity";
import { machinePath } from "../../lib/appNavigation";
import { DockerQueueBoard } from "../DockerQueueBoard";

/**
 * Home's capacity question: who holds the machine's CPU and memory, and who is
 * waiting behind them? The Machine page's own board, so the two read and
 * release leases identically; its loading, error and switched-off states come
 * with it. Polls only while Home is mounted.
 */
export function HomeMachineCapacity() {
  const docker = useDockerCapacity();
  return (
    <section className="home-capacity" aria-label="Machine capacity">
      <DockerQueueBoard
        status={docker.status}
        error={docker.error}
        loading={docker.loading}
        onChanged={docker.reload}
        headerSlot={
          <Link className="home-section-link" to={machinePath("capacity")}>
            Machine →
          </Link>
        }
      />
    </section>
  );
}
