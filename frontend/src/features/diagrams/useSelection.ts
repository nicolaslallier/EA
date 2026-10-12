// Which boxes are selected on the canvas.
//
// The *one* element whose detail is shown stays in the URL (`?element=`,
// docs/adr/0011); a set of boxes picked to be moved, aligned or removed
// together is a working state of this editing session and is not shared.
import { ref } from 'vue'

export function useSelection() {
  const ids = ref<string[]>([])

  function set(next: string[]): void {
    ids.value = [...new Set(next)]
  }

  function toggle(id: string): void {
    ids.value = ids.value.includes(id) ? ids.value.filter((other) => other !== id) : [...ids.value, id]
  }

  function clear(): void {
    ids.value = []
  }

  return { ids, set, toggle, clear }
}
