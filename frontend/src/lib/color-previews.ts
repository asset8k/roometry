import type { RoomScene } from './scene.generated'

/** Local visual overrides only; preserve source data and locked object colors. */
export function applyColorPreviews(scene: RoomScene, colors: Readonly<Record<string, string>>): RoomScene {
  return {
    ...scene,
    objects: scene.objects.map(object => {
      const color = colors[object.id]
      if (object.locked || typeof color !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(color)) return object
      return { ...object, color: color.toUpperCase() }
    }),
  }
}
