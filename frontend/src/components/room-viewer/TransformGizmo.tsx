import { useEffect, useLayoutEffect, useRef } from 'react'
import type { ComponentRef } from 'react'
import { TransformControls } from '@react-three/drei'
import { useThree } from '@react-three/fiber'
import { Group } from 'three'
import type { SceneObject } from '../../lib/scene.generated'
import type { EditorState, SceneEditor } from '../../lib/scene-editor'
import { gizmoEdit } from './gizmo-edit'


export default function TransformGizmo({ object, editor, state, onInteraction }: {
  object: SceneObject; editor: SceneEditor; state: EditorState; onInteraction: () => void
}) {
  const proxy = useRef(new Group())
  const controls = useRef<ComponentRef<typeof TransformControls>>(null)
  const wasDragging = useRef(false)
  const invalidate = useThree(store => store.invalidate)
  const canvas = useThree(store => store.gl.domElement)
  useEffect(() => {
    const current = controls.current
    current?.connect(canvas)
    return () => current?.dispose()
  }, [canvas])
  useLayoutEffect(() => {
    const ended = wasDragging.current && !state.dragging
    wasDragging.current = state.dragging
    if (state.dragging) return
    if (ended && controls.current) {
      controls.current.reset()
      controls.current.detach()
      controls.current.attach(proxy.current)
    }
    proxy.current.position.set(object.position.x, object.position.y, object.position.z)
    proxy.current.rotation.set(0, object.rotation_y_rad, 0)
    proxy.current.updateMatrixWorld()
    invalidate()
  }, [object.id, object.position.x, object.position.y, object.position.z, object.rotation_y_rad, state.dragging, invalidate])

  return (
    <>
      <primitive object={proxy.current} />
      <TransformControls
        ref={controls}
        domElement={canvas}
        object={proxy}
        mode={state.mode === 'move' ? 'translate' : 'rotate'}
        space="world"
        enabled={!state.busy}
        showX={state.mode === 'move'}
        showY={state.mode === 'rotate'}
        showZ={state.mode === 'move'}
        translationSnap={0.05}
        rotationSnap={Math.PI / 12}
        size={0.85}
        onMouseDown={() => { onInteraction(); editor.beginDrag() }}
        onObjectChange={() => editor.previewDrag(gizmoEdit(object, proxy.current, state.mode))}
        onMouseUp={() => { onInteraction(); void editor.finishDrag(gizmoEdit(object, proxy.current, state.mode)) }}
      />
    </>
  )
}
