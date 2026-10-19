import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { loadExampleScene, loadAlternativeScene } from '../../lib/scene'
import { SceneEditor } from '../../lib/scene-editor'
import ObjectInspector from './ObjectInspector'
import ObjectList from './ObjectList'

describe('comparison inspection controls', () => {
  it.each(['before', 'after'] as const)('renders %s movement fields with the correct permissions', view => {
    const editor = new SceneEditor(loadAlternativeScene(), vi.fn(), loadExampleScene())
    editor.switchView(view)
    editor.select('desk_1')
    const state = editor.getSnapshot()
    const object = state.scene.objects.find(item => item.id === 'desk_1')!
    const html = renderToStaticMarkup(<ObjectInspector scene={state.scene} object={object} editor={editor} state={state} />)
    const inputs = html.match(/<input\b[^>]*>/g)!
    expect(inputs).toHaveLength(3)
    expect(inputs.every(input => input.includes('disabled=""'))).toBe(view === 'before')
    if (view === 'before') expect(html).toContain('This layout is read-only. Switch to the editable layout to make changes.')
  })

  it.each([true, false])('protects original colors when readOnly=%s while retaining locked protection', readOnly => {
    const html = renderToStaticMarkup(<ObjectList
      scene={loadExampleScene()} readOnly={readOnly} hasColorPreviews={true}
      selectedId={null} disabledSelection={false} onSelect={vi.fn()}
      onColorChange={vi.fn()} onResetColors={vi.fn()}
    />)
    const inputs = html.match(/<input\b[^>]*>/g)!
    expect(inputs).toHaveLength(9)
    expect(inputs.find(input => input.includes('Color for bed'))).toContain('disabled=""')
    for (const label of ['desk', 'tv', 'rug']) {
      expect(inputs.find(input => input.includes(`Color for ${label}`))!.includes('disabled=""')).toBe(readOnly)
    }
    if (readOnly) expect(inputs.every(input => input.includes('disabled=""'))).toBe(true)
    const reset = html.match(/<button\b[^>]*>Reset colors<\/button>/)![0]
    expect(reset.includes('disabled=""')).toBe(readOnly)
    // Read-only still permits inspection through object selection.
    const selection = html.match(/<button\b[^>]*aria-label="Select desk"[^>]*>/)![0]
    expect(selection).not.toContain('disabled=""')
  })
})
