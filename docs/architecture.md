# Architecture Diagrams

This document visualizes the current proposed method after the Dual-DAG runtime integration. The key architectural change is that `DualDAGTaskStore` is the runtime source of truth, while `type_define.Graph` remains a compatibility projection.

## Current Architecture

```mermaid
flowchart TD
    A[User Goal / Minecraft Config] --> B[TaskManager]
    B --> C[LLM Task Decomposition]
    C --> D[DualDAGTaskStore]

    D --> D1[runtime_task Nodes]
    D --> D2[precedes_task Edges]
    D --> D3[Lifecycle Status]
    D --> D4[Candidate / Assigned Agents]

    D --> E[Task Graph Projection]
    E --> F[Legacy Task / Graph APIs]
    E --> G[Prompts / Logs / task_graph_snapshot.json]

    D --> H[GlobalController]
    H --> I[Dual-DAG Task Selection / Ranking]
    I --> J[BaseAgent]
    J --> K[Minecraft Env / Tools]

    K --> L[Action Log / Observations]
    L --> M[Dual-DAG Artifacts]
    D --> M

    M --> N[runtime_dual_dag_snapshot.json]
    M --> O[dual_dag_artifact.json]
    M --> P[decision_support.json]
    M --> Q[summary.json / metrics.json]
```

## Runtime Responsibility View

```mermaid
flowchart LR
    subgraph Canonical[Canonical Runtime State]
        D[DualDAGTaskStore]
        D --> T[runtime_task nodes]
        D --> E[precedes_task edges]
        D --> S[lifecycle: unknown / running / success / failure / blocked]
        D --> A[assignment metadata]
    end

    subgraph Compatibility[Compatibility Projection]
        G[Task Graph Projection]
        TSK[type_define.Task]
        GR[type_define.Graph]
        G --> TSK
        G --> GR
    end

    subgraph Control[Runtime Control]
        TM[TaskManager]
        GC[GlobalController]
        BA[BaseAgent]
        ENV[VillagerBench / Minecraft]
    end

    TM --> D
    D --> TM
    D --> G
    TM --> GC
    GC --> D
    GC --> BA
    BA --> ENV
    ENV --> GC

    subgraph Artifacts[Artifacts]
        RDS[runtime_dual_dag_snapshot.json]
        TGS[task_graph_snapshot.json]
        DDS[dual_dag_artifact.json]
        DS[decision_support.json]
        SUM[summary.json / metrics.json]
    end

    D --> RDS
    G --> TGS
    ENV --> DDS
    DDS --> DS
    DS --> SUM
    RDS --> SUM
```

## Dual-DAG Internal Operation

This view zooms into `DualDAGTaskStore`, excluding the rest of the controller architecture. It shows how runtime task state moves through the store from LLM decomposition to scheduling, lifecycle updates, and exported artifacts.

```mermaid
flowchart TD
    A["LLM Decomposition Result<br/>list[type_define.Task]"] --> B[load_tasks_from_decomposition]

    subgraph Store[DualDAGTaskStore: Canonical Runtime Task State]
        B --> C[upsert_task]
        C --> N[(nodes\nruntime_task)]
        B --> D[add_task_dependency]
        D --> E[(edges\nprecedes_task)]

        N --> O[query_open_tasks]
        E --> O
        O --> P[project Task objects]
        P --> Q[attach unfinished predecessors]
        Q --> R[query_runnable_tasks]
        R --> S{status unknown?\npredecessors done?\nagents available?}

        S -->|yes| T[Runnable Task]
        S -->|no| U[Open But Blocked Task]

        T --> V[mark_task_running]
        V --> W[(lifecycle.status=running\nassigned_agents)]
        W --> X[Agent / Env Execution]
        X --> Y{task result}
        Y -->|success| Z[mark_task_success]
        Y -->|failure| AA[mark_task_failure]
        Z --> AB[(lifecycle.status=success\nreflect feedback)]
        AA --> AC[(lifecycle.status=failure\nreflect feedback)]

        N --> AD[to_task_graph_projection]
        E --> AD
        N --> AE[snapshot]
        E --> AE
        AB --> AE
        AC --> AE
    end

    AD --> AF[type_define.Graph\ncompatibility only]
    AE --> AG[runtime_dual_dag_snapshot.json]
```

## Runtime Task Node Schema

Each runtime task is represented as a Dual-DAG node. The task graph view is rebuilt from these nodes, so this schema is the authoritative state shape for runtime task lifecycle.

```mermaid
flowchart LR
    subgraph P[Predecessor RuntimeTaskNode]
        P1["node_id = runtime:task:&lt;id&gt;"]
        P2["node_type = runtime_task"]
        P3["content: description, metadata, milestones, reflect"]
        P4["lifecycle: status, candidate_agents, assigned_agents, required_agent_count, available"]
        P5["provenance.source"]
    end

    subgraph E[PrecedesTaskEdge]
        E1["source_id"]
        E2["target_id"]
        E3["edge_type = precedes_task"]
        E4["metadata.source"]
    end

    subgraph S[Successor RuntimeTaskNode]
        S1["node_id = runtime:task:&lt;id&gt;"]
        S2["same content / lifecycle shape"]
    end

    P --> E --> S
```

Status values used by the runtime task store:

- `unknown`: created but not yet assigned or completed.
- `running`: selected by the controller and assigned to one or more agents.
- `success`: completed successfully; downstream dependencies may become runnable.
- `failure`: terminal failure; the overall terminal state becomes failure.

## Dependency And Runnable Semantics

`precedes_task` means the target task depends on the source task. A task is runnable only when it is still `unknown`, all transitive predecessors are successful, and enough free agents match the candidate set.

```mermaid
flowchart LR
    subgraph Dependency[DAG Dependency Semantics]
        T1[Task A\nstatus=success] -->|precedes_task| T2[Task B\nstatus=unknown]
        T2 -->|precedes_task| T3[Task C\nstatus=unknown]
    end

    T2 --> D1[direct_predecessor_ids\nA]
    T3 --> D2[direct_predecessor_ids\nB]
    T3 --> D3[all_predecessor_ids\nB, A]

    D1 --> R1{B runnable?}
    R1 -->|A success| YES[yes, if agents available]

    D3 --> R2{C runnable?}
    R2 -->|B still unknown| NO[no, blocked by unfinished predecessor]
```

Runnable-task filtering follows this decision order:

```mermaid
flowchart TD
    A[Task from query_open_tasks] --> B{status == unknown?}
    B -->|no| X[not runnable]
    B -->|yes| C{unfinished transitive predecessors?}
    C -->|yes| X
    C -->|no| D{free_agent_names provided?}
    D -->|no| Y[runnable]
    D -->|yes| E{candidate/free intersection\n>= required_agent_count?}
    E -->|yes| Y
    E -->|no| X
```

## Lifecycle Write-Back Loop

The controller does not mutate the compatibility graph as an authority. It writes lifecycle changes through `TaskManager`, which updates Dual-DAG and then rebuilds `TaskManager.graph` as a projection.

```mermaid
sequenceDiagram
    participant GC as GlobalController
    participant TM as TaskManager
    participant DD as DualDAGTaskStore
    participant GP as Task Graph Projection
    participant AG as Agent / Env

    GC->>TM: query_subtask_list()
    TM->>DD: query_open_tasks() / query_runnable_tasks()
    DD-->>TM: projected Task candidates
    TM-->>GC: candidate tasks

    GC->>TM: mark_task_running(task, agents)
    TM->>DD: mark_task_running(task.id, agents)
    DD-->>DD: lifecycle.status = running
    TM->>DD: to_task_graph_projection()
    DD-->>GP: regenerated Graph

    GC->>AG: execute selected task
    AG-->>GC: success or failure feedback

    alt success
        GC->>TM: mark_task_status(task.id, success, feedback)
        TM->>DD: mark_task_status(...)
        DD-->>DD: lifecycle.status = success
    else failure
        GC->>TM: mark_task_status(task.id, failure, feedback)
        TM->>DD: mark_task_status(...)
        DD-->>DD: lifecycle.status = failure
    end

    TM->>DD: to_task_graph_projection()
    DD-->>GP: regenerated compatibility Graph
```

## Snapshot And Projection Boundary

Dual-DAG emits the canonical runtime snapshot. The legacy graph snapshot is derived and should be interpreted as a compatibility artifact.

```mermaid
flowchart TB
    subgraph Canonical[Canonical]
        N[(runtime_task nodes)]
        E[(precedes_task edges)]
        L[(lifecycle fields)]
    end

    N --> S[snapshot]
    E --> S
    L --> S
    S --> R[runtime_dual_dag_snapshot.json\nsource_of_truth=dual_dag]

    N -.-> P[to_task_graph_projection]
    E -.-> P
    L -. status copied .-> P
    P -.-> G[task_graph_snapshot.json\ncompatibility projection]
```

## Before / After

```mermaid
flowchart TB
    subgraph Before[Before: Task Graph As Source Of Truth]
        B1[TaskManager.query_graph]
        B2[type_define.Graph]
        B3[GlobalController]
        B4[Dual-DAG Artifact / Ranking]
        B5[Task Graph Artifacts]

        B1 --> B2
        B2 --> B3
        B3 --> B2
        B2 -. read-only projection .-> B4
        B2 --> B5
    end

    subgraph After[After: Dual-DAG As Source Of Truth]
        A1[TaskManager Decomposition]
        A2[DualDAGTaskStore]
        A3[GlobalController]
        A4[Task Graph Projection]
        A5[Runtime Dual-DAG Artifact]
        A6[Compatibility Task Graph Artifact]

        A1 --> A2
        A2 --> A3
        A3 --> A2
        A2 --> A4
        A2 --> A5
        A4 --> A6
    end
```

## Paper Figure Layout

Use this layout for a paper or slide figure. It is intentionally not tied to Mermaid syntax, so it can be redrawn in TikZ, SVG, Illustrator, or Keynote.

```text
+--------------------------------------------------------------------------------+
|                                 Proposed Method                                |
+--------------------------------------------------------------------------------+
| Input                                                                          |
|   User Goal / Minecraft Config                                                 |
|        |                                                                       |
|        v                                                                       |
| Task Generation                                                                |
|   TaskManager + LLM Decomposition                                              |
|        |                                                                       |
|        v                                                                       |
| Canonical Runtime State                                                        |
|   DualDAGTaskStore                                                             |
|     - runtime_task nodes                                                       |
|     - precedes_task edges                                                      |
|     - lifecycle state                                                          |
|     - agent assignment metadata                                                |
|        |                              |                                        |
|        v                              v                                        |
| Controller Loop                  Compatibility Projection                      |
|   GlobalController                type_define.Graph / Task                     |
|   BaseAgent                       task_graph_snapshot.json                     |
|   Minecraft Tools                                                             |
|        |                                                                       |
|        v                                                                       |
| Evidence / Artifacts                                                           |
|   action_log.json                                                              |
|   runtime_dual_dag_snapshot.json                                               |
|   dual_dag_artifact.json                                                       |
|   decision_support.json                                                        |
|   summary.json / metrics.json                                                  |
+--------------------------------------------------------------------------------+
```

Suggested visual encoding:

- Use a thick border around `DualDAGTaskStore` to indicate source-of-truth ownership.
- Use dashed arrows from `DualDAGTaskStore` to `Task Graph Projection` to show derived compatibility state.
- Use solid arrows for lifecycle writes from `GlobalController` back to `DualDAGTaskStore`.
- Use a muted color for legacy `Task` / `Graph` APIs.
- Use a separate artifact lane at the bottom for reproducibility outputs.

## TikZ Sketch

The following is a compact TikZ skeleton that can be pasted into a LaTeX paper and styled further.

```tex
\begin{tikzpicture}[
  node distance=1.2cm and 1.5cm,
  box/.style={draw, rounded corners, align=center, minimum width=3.2cm, minimum height=0.9cm},
  source/.style={box, very thick, fill=blue!8},
  compat/.style={box, dashed, fill=gray!8},
  artifact/.style={box, fill=green!8},
  arrow/.style={->, thick}
]
\node[box] (input) {User Goal /\\Minecraft Config};
\node[box, below=of input] (tm) {TaskManager +\\LLM Decomposition};
\node[source, below=of tm] (dag) {DualDAGTaskStore\\Source of Truth};
\node[box, below left=of dag] (ctrl) {GlobalController\\BaseAgent};
\node[compat, below right=of dag] (graph) {Task Graph\\Projection};
\node[box, below=of ctrl] (env) {VillagerBench /\\Minecraft Tools};
\node[artifact, below=of dag, yshift=-2.4cm] (artifacts) {runtime\_dual\_dag\_snapshot.json\\dual\_dag\_artifact.json\\summary / metrics};

\draw[arrow] (input) -- (tm);
\draw[arrow] (tm) -- (dag);
\draw[arrow] (dag) -- (ctrl);
\draw[arrow] (ctrl) -- (env);
\draw[arrow] (env.west) .. controls +(-1.2,-0.2) and +(-1.2,0.2) .. (ctrl.west);
\draw[arrow] (ctrl) -- node[left]{lifecycle writes} (dag);
\draw[arrow, dashed] (dag) -- (graph);
\draw[arrow] (dag) -- (artifacts);
\draw[arrow, dashed] (graph) -- (artifacts);
\end{tikzpicture}
```

## Current Guarantees

- `DualDAGTaskStore` owns runtime task lifecycle and dependency state.
- `TaskManager.graph` is regenerated from Dual-DAG state.
- `GlobalController` writes task running/success/failure state through `TaskManager` into Dual-DAG.
- Minecraft benchmark runs always emit `runtime_dual_dag_snapshot.json`.
- `task_graph_snapshot.json` is retained as a compatibility projection.
