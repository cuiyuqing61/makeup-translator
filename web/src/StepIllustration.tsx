import { useId } from 'react'
import type { StepIllustration, TutorialStep } from './types'

function FaceBase() {
  return <g className="diagram-line">
    <path d="M111 30 Q160 8 209 30 Q234 60 224 111 Q218 172 160 192 Q102 172 96 111 Q86 60 111 30Z" fill="#fff9f4" />
    <path d="M104 69 Q108 18 154 18 Q205 13 216 69" fill="none" />
    <path d="M112 96 Q128 87 145 97 M175 97 Q192 87 208 96" fill="none" />
    <path d="M113 80 Q130 73 145 79 M175 79 Q190 73 207 80" fill="none" />
    <path d="M160 99 Q153 132 161 137" fill="none" />
    <path d="M139 156 Q160 167 181 156" fill="none" />
  </g>
}

function EyeBase({ brow = false }: { brow?: boolean }) {
  return <g className="diagram-line">
    <path d="M75 82 Q140 43 232 68" fill="none" strokeWidth={brow ? 10 : 6} strokeLinecap="round" />
    <path d="M75 116 Q151 62 239 112 Q161 150 75 116Z" fill="#fff9f4" />
    <circle cx="158" cy="111" r="24" fill="#eee1d9" />
    <circle cx="158" cy="111" r="12" fill="#6e6266" />
    <path d="M75 116 Q151 62 239 112" fill="none" strokeWidth="3" />
    <path d="M84 136 Q160 165 231 132" fill="none" />
    <path d="M60 188 Q155 171 257 188" fill="none" opacity=".5" />
  </g>
}

function LipBase() {
  return <g className="diagram-line">
    <path d="M66 111 Q93 96 125 99 Q145 76 160 94 Q179 76 198 99 Q228 96 254 111 Q207 153 160 149 Q111 153 66 111Z" fill="#f9e6e5" />
    <path d="M66 111 Q111 119 160 115 Q211 119 254 111" fill="none" />
    <path d="M112 70 Q160 60 208 70 M111 172 Q160 182 209 172" fill="none" opacity=".45" />
  </g>
}

export function StepIllustrationDiagram({ step, illustration }: {
  step: TutorialStep
  illustration?: StepIllustration
}) {
  const uid = useId().replace(/:/g, '')
  const markerId = `diagram-arrow-${uid}`
  const titleId = `diagram-title-${uid}`
  const descriptionId = `diagram-description-${uid}`
  const template = illustration?.template ?? (
    step.region === 'eye' ? 'eye' : step.region === 'eyebrow' ? 'brow' : step.region === 'lip' ? 'lip' : 'face'
  )
  const placement = illustration?.placement ?? '此部位通用示意'
  const direction = illustration?.direction ?? '具体位置和动作请以文字步骤为准'
  const amount = illustration?.amount ?? '按文字步骤少量操作'
  const simplified = illustration?.mode === 'simplified'
  return <figure className={`step-illustration${simplified ? ' is-simplified' : ''}`}>
    <svg viewBox="0 0 320 210" role="img" aria-labelledby={`${titleId} ${descriptionId}`}>
      <title id={titleId}>步骤 {step.order}：{step.goal}的位置和方向示意</title>
      <desc id={descriptionId}>粉色区域：{placement}。箭头方向：{direction}。用量：{amount}。示意图，不代表参考图中的真实上妆步骤。</desc>
      <defs><marker id={markerId} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M1 1 L7 4 L1 7" fill="none" stroke="#9d4b70" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></marker></defs>
      <rect x="0" y="0" width="320" height="210" rx="18" fill="#fcf5f3" />
      {template === 'face' ? <FaceBase /> : template === 'lip' ? <LipBase /> : <EyeBase brow={template === 'brow'} />}
      <g className="diagram-area">{illustration?.areas.map((area, index) =>
        <ellipse key={index} cx={area.cx} cy={area.cy} rx={area.rx} ry={area.ry}
          transform={`rotate(${area.rotation} ${area.cx} ${area.cy})`} />)}</g>
      <g className="diagram-arrow">{illustration?.arrows.map((arrow, index) =>
        <line key={index} x1={arrow.x1} y1={arrow.y1} x2={arrow.x2} y2={arrow.y2} markerEnd={`url(#${markerId})`} />)}</g>
    </svg>
    <figcaption><strong>{simplified ? '简化示意' : '位置与方向'}</strong><span>{placement} · {direction}</span><small>{amount}。图为操作示意，请以文字步骤和实际舒适度为准。</small></figcaption>
  </figure>
}
