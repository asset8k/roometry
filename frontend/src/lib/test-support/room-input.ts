import type { InputLimits } from '../room-input'
import { parsePreparedInput } from '../room-input'

export const limits: InputLimits = {
  min_images: 1, max_images: 10, allowed_media_types: ['image/jpeg', 'image/png', 'image/webp', 'image/heic', 'image/heif', 'image/avif'],
  allowed_extensions: ['.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.avif'],
  max_file_bytes: 8388608, max_total_file_bytes: 83886080, max_body_bytes: 85983232,
  min_edge_px: 256, max_edge_px: 10000, max_pixels: 40000000,
  prepared_max_edge_px: 2048, prepared_max_bytes: 4194304, options_max_bytes: 16384,
  ttl_seconds: 3600, max_description_chars: 2000, max_goal_text_chars: 2000,
  max_constraints_chars: 1000, max_style_chars: 100, max_budget_amount: 1000000,
  max_room_side_m: 30, max_room_height_m: 10,
}
export const photo = (name = 'room.png', type = 'image/png') => new File(['photo bytes'], name, { type })
export function prepared(files: readonly File[] = [photo()], expires = Date.now() + 3600000) {
  return parsePreparedInput({
    input_id: '11111111-1111-4111-8111-111111111111', status: 'ready', options: { goals: ['general_analysis'] },
    images: files.map((file, index) => ({
      image_id: `22222222-2222-4222-8222-${index.toString(16).padStart(12, '0')}`,
      original_media_type: file.type || 'image/heic', original_bytes: file.size, original_width_px: 640, original_height_px: 480,
      prepared_media_type: 'image/jpeg', prepared_bytes: 1000, prepared_width_px: 640, prepared_height_px: 480,
    })), expires_at: new Date(expires).toISOString(),
  })
}
