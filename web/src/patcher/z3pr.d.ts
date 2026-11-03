declare module '@maseya/z3pr' {
  export function randomize(
    rom: Uint8Array,
    options: { randomize_overworld?: boolean; randomize_dungeon?: boolean; mode: string; seed: number },
  ): Uint8Array
}
