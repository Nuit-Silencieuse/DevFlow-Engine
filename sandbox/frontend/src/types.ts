export type StageName =
  | "REQUIREMENT_ANALYSIS"
  | "SYSTEM_DESIGN"
  | "CODE_GENERATION"
  | "TEST_GENERATION"
  | "CODE_REVIEW"
  | "DELIVERY_INTEGRATION";

export type CheckpointDecision = "APPROVE" | "REJECT";

export interface RepositoryContext {
  rootPath: string;
  includePaths: string[];
  excludePaths: string[];
  targetFiles: string[];
  maxFiles: number;
  maxBytes: number;
}

export interface CreatePipelineRequest {
  name: string;
  requirement: string;
  stages: string[];
  repository?: RepositoryContext;
}

export interface CreatePipelineResponse {
  pipelineId: string;
  status: string;
}

export interface StageStatusResponse {
  name: string;
  status: string;
  requiresHumanApproval: boolean;
  output: Record<string, unknown>;
}

export interface PipelineStatusResponse {
  pipelineId: string;
  status: string;
  currentStage: string;
  repository: RepositoryContext | null;
  stages: StageStatusResponse[];
}

export interface CheckpointDecisionResponse {
  status: string;
  message: string;
  stageName: string | null;
  stageOutput: Record<string, unknown>;
}
