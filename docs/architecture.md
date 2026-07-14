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
