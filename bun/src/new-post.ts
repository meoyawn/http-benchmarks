import * as v from "valibot"

const emailRule = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/

export const NewPost = v.object({
  content: v.pipe(v.string(), v.nonEmpty()),
  email: v.pipe(v.string(), v.check(email => emailRule.exec(email)?.[0] === email, "Invalid email")),
})

export type NewPost = v.InferOutput<typeof NewPost>
export const parseNewPost = v.safeParser(NewPost)
