export type StageName =
  | "REQUIREMENT_ANALYSIS"
  | "SYSTEM_DESIGN"
  | "CODE_GENERATION"
  | "TEST_GENERATION"
  | "APPLY_AND_RUN_TESTS"
  | "CODE_REVIEW"
  | "DELIVERY_INTEGRATION";

export type CheckpointDecision = "APPROVE" | "REJECT";

export interface RepositoryContext {
  rootPath: string;
  includePaths?: string[];
  excludePaths?: string[];
  targetFiles?: string[];
  maxRounds?: number;
  maxFiles?: number;
  maxBytes?: number;
  maxSearchResults?: number;
  privacyMode?: "standard" | "strict";
}

export interface CreatePipelineRequest {
  name: string;
  requirement: string;
  stages: string[];
  repository?: RepositoryContext;
  llmConfig?: LlmRuntimeConfig;
}

export interface LlmProviderConfig {
  provider?: string;
  baseUrl?: string;
  apiKey?: string;
  credentialId?: string;
  model?: string;
  timeoutSeconds?: number;
  maxTokens?: number;
  temperature?: number;
}

export interface LlmRuntimeConfig {
  defaultConfig?: LlmProviderConfig;
  stageOverrides?: Record<string, LlmProviderConfig>;
}

export interface LlmCredentialResponse {
  credentialId: string;
  provider: string;
  maskedApiKey: string;
}

export interface LlmConfigTestResponse {
  status: string;
  message: string;
  provider: string;
  model: string;
}

export interface LlmConfigFileResponse {
  path: string;
  config: Record<string, unknown>;
}

export interface CreatePipelineResponse {
  pipelineId: string;
  workflowId?: string;
  status: string;
}

export interface EvidenceItem {
  filePath: string;
  lineStart?: number | null;
  lineEnd?: number | null;
  symbolName?: string | null;
  excerpt?: string;
  relevanceReason?: string;
  supports?: string[];
}

export interface SkippedPath {
  path: string;
  reason: string;
  detail?: string;
}

export interface BudgetUsage {
  roundsUsed?: number;
  filesRead?: number;
  bytesRead?: number;
  searchesUsed?: number;
}

export interface ExplorationStep {
  stepIndex?: number;
  roundIndex?: number;
  actionType: string;
  reason?: string;
  resultSummary?: string;
  selectedFiles?: string[];
}

export interface CodeContextSummary {
  status?: string;
  rootPath?: string;
  inspectedFiles?: string[];
  searchQueries?: string[];
  candidateFiles?: string[];
  evidence?: EvidenceItem[];
  skippedPaths?: SkippedPath[];
  budgetUsage?: BudgetUsage;
  confidence?: number;
  openQuestions?: string[];
  notes?: string[];
  explorationTrace?: ExplorationStep[];
}

export interface StageOutput {
  codeContext?: CodeContextSummary;
  explorationTrace?: ExplorationStep[];
  [key: string]: unknown;
}

export interface StageStatusResponse {
  name: string;
  status: string;
  requiresHumanApproval: boolean;
  output: StageOutput;
  outputAvailable?: boolean;
  artifactRevision?: string;
}

export interface PipelineStatusResponse {
  pipelineId: string;
  workflowId?: string;
  status: string;
  currentStage: string;
  repository: RepositoryContext | null;
  stages: StageStatusResponse[];
}

export interface StageSummaryResponse {
  name: string;
  status: string;
  requiresHumanApproval: boolean;
  outputAvailable: boolean;
  artifactRevision: string;
}

export interface PipelineSummaryResponse {
  pipelineId: string;
  workflowId?: string;
  status: string;
  currentStage: string;
  repository: RepositoryContext | null;
  updatedAt?: string;
  stages: StageSummaryResponse[];
}

export interface StageArtifactResponse {
  pipelineId: string;
  stageName: string;
  status: string;
  requiresHumanApproval: boolean;
  artifactRevision: string;
  output: StageOutput;
}

export interface CheckpointDecisionResponse {
  status: string;
  message: string;
  stageName: string | null;
  stageOutput: Record<string, unknown>;
}
