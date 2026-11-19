export type ProductStatus = 'draft' | 'pending_user_review' | 'confirmed'

export interface SourceValidation {
  reachable: boolean
  http_status: number | null
  final_url: string | null
  official_domain_verified: boolean
}

export interface ProductSource {
  title: string
  url: string
  source_type: 'official' | 'official_store' | 'retailer' | 'review' | 'other'
  supports_fields: string[]
  note: string | null
  validation: SourceValidation | null
}

export interface SearchAudit {
  status: 'completed' | 'partial' | 'timeout' | 'failed'
  elapsed_ms: number
  crossed_foreground_boundary: boolean
}

export interface ProductEnrichment {
  field_evidence?: Record<string, {url: string; excerpt: string; checked_at: string}>
  image_comparison?: string | null
  match_status: 'exact_match' | 'likely_match' | 'ambiguous'
  canonical_brand: string
  canonical_name: string
  model_or_shade: string | null
  market: string | null
  category: string
  color_family: string[]
  finish: string[]
  official_description: string | null
  official_usage: string | null
  alternatives?: {brand:string; product_name:string; model_or_shade:string|null; market:string|null; url:string; difference:string}[]
  review_observations: string[]
  unresolved_fields: string[]
  conflicts: string[]
  validation_warnings: string[]
  sources: ProductSource[]
  search_audit: SearchAudit | null
}

export interface ProductRecord {
  photo_urls?: string[]
  opened_photo_url?: string | null
  opened_photo_version?: string | null
  color_samples?: ColorSample[]
  color_task_id?: string | null
  color_error?: string | null
  id: string
  status: ProductStatus
  raw_brand: string
  raw_name: string
  raw_model_or_shade: string | null
  raw_market: string | null
  enrichment: ProductEnrichment | null
  created_at: string
  updated_at: string
}

export interface ColorSample {
  label: string
  hex: string
  source: 'product_photo' | 'reference_photo'
  confidence: 'high' | 'medium' | 'low'
  box: number[]
}

export interface MakeupFeature {
  region: 'complexion' | 'eyebrow' | 'eye' | 'cheek' | 'lip'
  effect: string
  color_family: string[]
  finish: string[]
  placement: string
  intensity: 'light' | 'medium' | 'strong'
  evidence: string
  confidence: 'high' | 'medium' | 'low'
  color_samples?: ColorSample[]
  color_boxes?: number[][]
}

export interface MakeupTarget {
  id: string
  region: MakeupFeature['region']
  role: string
  effect: string
  placement: string
  evidence: string
  confidence: 'high' | 'medium' | 'low'
  color_family: string[]
  finish: string[]
  color_samples: ColorSample[]
}

export interface TutorialStep {
  order: number
  step_id?: string | null
  technique_card_id: string
  technique_review_status?: 'draft' | 'reviewed' | 'tested' | null
  region: MakeupFeature['region']
  goal: string
  instruction: string
  success_signal: string
  product_match: {
    required_category: string
    selected_product_id: string | null
    match_reason: string
    status: 'owned_match' | 'owned_approximation' | 'catalog_candidate' | 'missing' | 'not_needed' | 'pending'
    selected_color_label?: string | null
    color_evidence?: 'photo' | 'metadata' | 'none'
    catalog_suggestions?: string[]
    catalog_product_id?: string | null
    catalog_product_name?: string | null
    catalog_source_url?: string | null
  }
  product_requirement?: {category: string; purpose: string; suitable_forms: string[]} | null
  target_id?: string | null
  target_role?: string | null
  target_evidence?: string | null
  face_evidence?: string[]
  application?: {
    placement: string
    direction: string
    amount: string
    layers: string
    action: string
    completion_check: string
  } | null
  drawing?: {anchor: string; side: string; offset_x: number; offset_y: number; radius_x: number; radius_y: number; direction: string; strength: string; layers: number} | null
  tool: string | null
  adjustment_reason: string
  caution: string | null
  technique_source_title?: string | null
  technique_source_url?: string | null
  technique_source_publisher?: string | null
  technique_source_section?: string | null
}

export interface StepIllustration {
  version: '1'
  step_order: number
  source_card_id: string
  template: 'face' | 'eye' | 'brow' | 'lip'
  placement: string
  direction: string
  amount: string
  areas: {cx: number; cy: number; rx: number; ry: number; rotation: number}[]
  arrows: {x1: number; y1: number; x2: number; y2: number}[]
  mode: 'standard' | 'simplified'
}

export interface PipelineResult {
  analysis: {
    provider: string
    overall_style: string[]
    signature_features: string[]
    features: MakeupFeature[]
    targets?: MakeupTarget[]
    uncertain_or_invisible: string[]
    safety_notes: string[]
  }
  retrieved_card_ids: string[]
  step_illustrations?: StepIllustration[]
  tutorial: {
    provider: string
    title: string
    look_summary: string
    preserved_features: string[]
    personalized_changes: string[]
    steps: TutorialStep[]
    missing_products: string[]
    uncertainty_notice: string
    estimated_minutes?: number | null
    review_warnings: string[]
  }
}

export interface Health {
  status: 'ok'
  qwen_configured: boolean
}

export type TaskStatus = 'queued' | 'running' | 'waiting_user' | 'succeeded' | 'failed' | 'timed_out' | 'cancelled'

export interface BackgroundTask {
  clarification?: {question: string; choices: string[]; round: number} | null
  trace?: {name: string; status: string; duration_ms: number}[]
  id: string
  kind: 'tutorial' | 'tutorial_analysis' | 'tutorial_steps' | 'tutorial_products' | 'product_enrichment' | 'product_color' | 'profile_analysis' | 'base_image' | 'step_image'
  status: TaskStatus
  progress: number
  stage: string
  message: string
  target_id: string | null
  result: Record<string, unknown> | null
  error: string | null
  queue_position?: number
  queued_seconds?: number
  elapsed_seconds: number
  foreground_wait_seconds: number
  timeout_seconds: number
  continued_in_background: boolean
  cancellable: boolean
}
