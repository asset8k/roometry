import fixture from './fixtures/bedroom_scene.json'
import afterFixture from './fixtures/bedroom_after_scene.json'
import { parseRoomScene } from '../scene'

export function loadExampleScene() {
  return parseRoomScene(structuredClone(fixture))
}

export function loadAlternativeScene() {
  return parseRoomScene(structuredClone(afterFixture))
}
