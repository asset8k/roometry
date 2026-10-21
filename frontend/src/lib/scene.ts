import Ajv2020 from 'ajv/dist/2020.js'
import schema from './scene.schema.json'
import type { RoomScene } from './scene.generated'

const validator = new Ajv2020({ allErrors: true, strict: true })
const validate = validator.compile<RoomScene>(schema)

/** Validate JSON shape before rendering. Physical checks remain server-side. */
export function parseRoomScene(data: unknown): RoomScene {
  if (!validate(data)) {
    throw new Error(`Invalid scene: ${validator.errorsText(validate.errors)}`)
  }
  return data
}
