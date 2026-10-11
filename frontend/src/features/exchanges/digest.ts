// L'empreinte d'un ou plusieurs fichiers, calculée comme le serveur (`digest_of`).
//
// Un fichier : le SHA-256 de ses octets, ce que `shasum -a 256` affiche. Plusieurs
// (les trois CSV) : chaque partie précédée de sa longueur sur 8 octets
// gros-boutiste. Le bouton « Confirmer » compare cette empreinte à celle du
// rapport de la simulation : c'est ce qui dit qu'on confirme le fichier simulé.

function bytesOf(file: File): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer))
    reader.onerror = () => reject(reader.error ?? new Error('lecture impossible'))
    reader.readAsArrayBuffer(file)
  })
}

function joined(parts: Uint8Array[]): Uint8Array {
  if (parts.length === 1) {
    return parts[0]
  }
  const out = new Uint8Array(parts.reduce((total, part) => total + 8 + part.length, 0))
  const view = new DataView(out.buffer)
  let offset = 0
  for (const part of parts) {
    view.setBigUint64(offset, BigInt(part.length))
    out.set(part, offset + 8)
    offset += 8 + part.length
  }
  return out
}

export async function sha256Of(files: File[]): Promise<string> {
  const body = joined(await Promise.all(files.map(bytesOf)))
  const digest = await crypto.subtle.digest('SHA-256', body as BufferSource)
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}
