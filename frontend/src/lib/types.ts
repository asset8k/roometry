export interface HealthResponse {
  status: 'ok'
  service: 'roometry'
}

export type {
  RoomScene, RoomDimensions, SceneObject, ObjectDimensions, Position, Opening,
} from './scene.generated'
